# Runbook локального запуска и диагностики

## Запуск через Docker Compose

Требуются Docker и Compose plugin. Из корня проекта:

```bash
docker compose up --build -d
docker compose ps
```

API при старте выполняет `alembic upgrade head`. Worker и frontend запускаются
после того, как PostgreSQL и API прошли health checks. По умолчанию приложение
доступно на <http://localhost:5173>, Swagger — на <http://localhost:8000/docs>.

Быстрая проверка состояния:

```bash
curl --fail --silent --show-error http://localhost:8000/health/live
curl --fail --silent --show-error http://localhost:8000/health/ready
curl --fail --silent --show-error http://localhost:8000/api/v1/import-formats
curl --fail --silent --show-error http://localhost:5173
curl --fail --silent --show-error http://localhost:5173/api/v1/import-formats
```

`live` подтверждает только работу процесса API. `ready` дополнительно выполняет
запрос к PostgreSQL. Запрос import formats через `5173` проверяет и Node static
server, и внутренний frontend-to-API proxy.

Порты можно изменить без редактирования Compose-файла:

```bash
FRONTEND_PORT=15173 API_PORT=18000 POSTGRES_PORT=15432 docker compose up --build -d
```

Если `5173` уже занят локальным `npm run dev`, остановить его через `Ctrl+C` или
запустить Compose с другим host-портом:

```bash
FRONTEND_PORT=5180 docker compose up --build -d
```

Внутри Docker frontend всё равно слушает `5173`; меняется только опубликованный
порт на host.

Остановить процессы, сохранив PostgreSQL и исходные импорты:

```bash
docker compose down
```

Команда `docker compose down --volumes` безвозвратно удаляет локальную БД и
хранилище импортов. Её следует использовать только для осознанного сброса
искусственных данных разработки.

## Локальный запуск Python

PostgreSQL можно оставить в Compose, а API и worker запустить локально:

```bash
docker compose up -d db
uv sync --locked
uv run alembic upgrade head
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

В отдельном терминале:

```bash
uv run python -m apps.worker.main
```

Настройки берутся из `FINANCE_*`. Безопасный перечень локальных переменных есть в
`.env.example`. Реальные токены, логины, номера счетов и финансовые файлы нельзя
помещать в `.env`, документацию или рабочую директорию проекта.

## Локальная разработка frontend

Compose уже запускает собранный frontend. Следующий вариант нужен только для hot
reload: frontend имеет отдельную workspace boundary и lockfile, требует Node
`22.17.1` и npm `10.9.2`. Сначала запустить backend без container frontend:

```bash
docker compose up -d db api worker
```

Затем:

```bash
cd frontend
npm ci
npm run api:check
npm run dev
```

Открыть <http://127.0.0.1:5173>. Vite proxy направляет только `/api` на
<http://127.0.0.1:8000>. Если API работает на другом origin:

```bash
VITE_API_BASE_URL=http://127.0.0.1:18000 npm run dev
```

После намеренного изменения OpenAPI generated artifacts обновляются из корня
проекта с установленным Python environment:

```bash
cd frontend
npm run api:generate
npm run api:check
```

`api:check` должен проходить без изменения tracked generated files. Финансовые
response types нельзя исправлять вручную в `src/api/schema.d.ts`.

## Диагностика

### API не запускается

Проверить состояние и последние безопасные логи:

```bash
docker compose ps
docker compose logs --tail=100 api db
```

Если `ready` возвращает 503, проверить PostgreSQL:

```bash
docker compose exec db pg_isready -U finance -d finance
docker compose logs --tail=100 db
```

Текущую миграцию работающего API можно проверить командой:

```bash
docker compose exec api alembic current
docker compose exec api alembic check
```

### Импорт не выходит из uploaded

Получить batch через `GET /api/v1/imports/{batch_id}` и проверить job status. Затем
проверить worker:

```bash
docker compose ps worker
docker compose logs --tail=100 worker
```

Worker выполняет `parse_import` и системный `fx_sync`. Import-ошибка сохраняется
как безопасный `last_error` и переводит batch в `failed`; полные исходные строки в
production-логи не выводятся. Compose использует для worker
`restart: unless-stopped`, поэтому неожиданное завершение автоматически
перезапускается. После явной остановки или изменения окружения worker можно
запустить снова:

```bash
docker compose up -d worker
```

Frontend заменяет обычный индикатор обработки диагностикой, если pending
`parse_import` не был взят worker за 30 секунд.

Failed job автоматически не возвращается в pending. Повторный upload того же
файла на тот же счёт вернёт существующий batch, поэтому ручное изменение статусов
в PostgreSQL не является штатным восстановлением.

### Публичный FX sync

`POST /api/v1/fx-rates/sync` только создаёт PostgreSQL job и не выполняет сеть в
API process. Worker получает официальный дневной USD/RUB из XML Банка России без
credentials и без передачи portfolio/account/import данных. Повторный enqueue в
минимальном интервале возвращает существующий job; повторное получение той же даты
не создаёт второе observation.

Статус доступен через `GET /api/v1/fx-rates/sync/{job_id}`. Ответ failed job
содержит только безопасный `error_code`; raw HTTP/XML provider response не
сохраняется и не логируется. После ошибки последнее валидное observation остаётся
доступным. Проверить выбор курса без сетевого запроса можно через:

```text
GET /api/v1/fx-rates/resolve?base_currency=USD&quote_currency=RUB&valuation_as_of=...
```

Resolver использует только `observed_at <= valuation_as_of`, возвращает age и
`fresh|stale|unavailable` и ограничен direct, inverse или одним pivot через RUB.
Отсутствующий rate не заменяется единицей. Официальный CBR rate является дневным,
а не intraday market quote. Manual fallback нескольких rates вводится одной
транзакцией через `POST /api/v1/fx-rates/batch`; несколько manual instrument prices
аналогично принимаются `/api/v1/prices/batch`. Один невалидный элемент откатывает
весь batch.

### Криптоцены Phase 05

Миграция `0016_market_price_provenance` добавляет происхождение цены и ссылку
расчётной позиции на наблюдение. Существующие ручные значения `NUMERIC` не
округляются; для них `provider=manual`, `price_kind=manual`,
`time_quality=user_supplied`, а `fetched_at` переносится из `created_at`.
Миграция `0017_market_mapping_and_job` добавляет глобальные проверенные
сопоставления и обязательную ссылку автоматической цены на такое сопоставление.
Ручные записи остаются без ссылки. Новый `market_data_sync` — глобальное задание
без `batch_id`; повторная постановка для того же provider/market объединяется.
Публичные `POST /api/v1/prices` и `/batch` по-прежнему создают только ручные
наблюдения. `GET /api/v1/prices` и `/resolve` показывают provenance;
`GET /api/v1/instruments/{instrument_id}/market-mappings` показывает статус
сопоставления и безопасную причину отсутствия автоматической цены. Откат `0017`
допустим только до появления автоматических цен и market-data jobs. Откат к
`0015_remove_allocation_targets` допустим только до появления автоматических
наблюдений; миграция откажет в downgrade после их записи, чтобы не удалить
новые цены. Перед обновлением рабочей базы сделать обычную резервную копию;
автоматические тесты запускаются только с `FINANCE_TEST_DATABASE_URL`, содержащим
`test` в имени БД.

Live-синхронизация CoinPaprika Free включена для BTC, ETH, SOL, USDT и USDC,
если внутренний `crypto_asset_code`, USD и имя точно совпадают с проверенным
каталогом. Сервис запрашивает один общий `/v1/tickers?quotes=USD` без данных
портфеля, по умолчанию раз в час; `FINANCE_MARKET_AUTO_SYNC_ENABLED=true`.
Интервал, таймаут и предельный размер ответа задаются
`FINANCE_MARKET_AUTO_SYNC_INTERVAL_SECONDS`,
`FINANCE_MARKET_HTTP_TIMEOUT_SECONDS`, `FINANCE_MARKET_RESPONSE_MAX_BYTES`.
В `FINANCE_ENVIRONMENT=test` и при worker `--once` автоматической постановки
нет. Последний безопасный status/counts задания виден через
`GET /api/v1/market-data/sync/latest`; ошибка источника не удаляет прежнюю
проверенную цену. Для акций и фондов MOEX live-путь не включён без договора.
Офлайн-контракт с синтетическим provider проверяет сохранение, идемпотентность,
конфликт, пересчёт и повтор после сбоя расчёта.
Сохранённая оценка открывается без сетевого обращения
к бирже; открытые обзор и активы проверяют новый snapshot каждые 30 секунд.

Defaults конфигурируются `FINANCE_FX_HTTP_TIMEOUT_SECONDS`,
`FINANCE_FX_MAX_ATTEMPTS`, `FINANCE_FX_RETRY_BACKOFF_SECONDS`,
`FINANCE_FX_RESPONSE_MAX_BYTES`, `FINANCE_FX_MIN_SYNC_INTERVAL_SECONDS`,
`FINANCE_FX_STALE_AFTER_SECONDS`, `FINANCE_MARKET_PRICE_STALE_AFTER_SECONDS` и
`FINANCE_CRYPTO_AGGREGATE_STALE_AFTER_SECONDS`. Время загрузки цены не
обновляет её свежесть.
Увеличение сетевых/resource limits требует отдельной проверки; provider URL не
принимается из HTTP input.

### Брокерский импорт через Swagger

В `POST /api/v1/imports` пользователь всегда явно выбирает существующий broker
account и один из provider IDs:

- `tbank_broker_xlsx` + `declared_format=xlsx` для официального XLSX
  Т‑Инвестиций;
- `alfa_broker_xml_import` + `declared_format=xml` для XML Альфа‑Инвестиций
  «для импорта».

Обычный XML с корнем `Report`/схемой `MyBroker` не эквивалентен варианту «XML для
импорта» с корнем `report_broker`. Он отклоняется с кодом
`alfa_xml_variant_unsupported` и понятной инструкцией получить правильный вариант.

Выбор provider не является доверием к имени файла: worker независимо проверяет
внутренний root/sheet, обязательные секции и версию. Корректный файл другого
provider завершится `failed` с безопасным `import_adapter_not_found`. Если те же
байты уже загружены на тот же account, account+SHA-256 дедупликация раньше
detection вернёт существующий batch с HTTP 200; для диагностики provider нельзя
обходить эту гарантию изменением БД.

После завершения parse получить `GET /api/v1/imports/{batch_id}/preview`. Верхний
уровень ответа содержит provider, detected format/version, период, completeness и
`reconciliation_status`; `summary` содержит operation counts, точные агрегаты по
валютам и counts устойчивых diagnostics. Reconciliation summary намеренно не
показывает instrument identifiers, контрольные суммы или raw строки.

`reconciliation_status=mismatch` означает расхождение дельт отчёта и
нормализованных движений. Это не исправляется скрытой adjustment и само по себе не
блокирует confirm: сначала следует рассмотреть diagnostics и неподдерживаемые
секции. `import_reconciliation_invalid` означает повреждённую внутреннюю metadata
и блокирует confirm целиком; частичных Ledger operations после такого ответа нет.

Строки Alfa `transfers` с неизвестной себестоимостью остаются warning до явного
review. В текущей версии их следует исключить с audit note; превращать transfer в
покупку или balance adjustment нельзя. Duplicate rows перекрывающегося отчёта
также рассматриваются явно: обычно уже импортированные строки исключаются, после
чего новые строки batch можно подтвердить.

### Confirm возвращает 409

Проверить preview и staging rows. Confirm допустим только после рассмотрения
warning/error/duplicate строк. Для review доступны исключение строки, явное
сопоставление инструмента и осознанное разрешение дубля. Повторный confirm уже
успешного batch безопасен и возвращает прежние operation IDs.

Если code равен `import_reconciliation_invalid`, не изменять reconciliation JSON
в PostgreSQL вручную. Повторно проверить исходный synthetic/обезличенный файл и
совместимость adapter version. Ошибка возникает до записи Ledger и должна
исправляться в adapter или миграции, а не разрешением строки.

### Пересчёт содержит diagnostics

Получить snapshot через `GET /api/v1/portfolios/{portfolio_id}/positions`.
Diagnostics не являются нулевыми подстановками: `market_price_missing` требует
ручной цены, `negative_position` указывает на недостаточную историю, а
`cost_basis_unknown` и currency mismatch требуют проверки исходных операций.

### Current analytics и allocation

После пересчёта получить overview через
`GET /api/v1/portfolios/{portfolio_id}/analytics/overview?reporting_currency=USD`
либо `RUB`. Holdings, breakdown и exposure используют тот же snapshot и known
denominator. `snapshot_fresh=false` означает, что после пересчёта Ledger изменился:
повторить `/positions/recalculate`, а не принимать partial snapshot за актуальный.

Категории и portfolio-specific overrides настраиваются через
`/allocation/categories` и `/allocation/instrument-categories/{instrument_id}`.
`GET /allocation` показывает фактическую стоимость и exact actual weight по
категориям. Partial coverage проверять по known denominator, included/excluded
counts и diagnostics. Target plan и `/allocation/targets` удалены из MVP;
frontend не пересчитывает веса самостоятельно.

### Event timelines через Swagger

`cash-flows`, `income`, `costs` и `trading` требуют inclusive `from`, exclusive
`to`, `day|week|month` и canonical IANA `timezone`. Один запрос охватывает не более
366 локальных календарных дней. Optional `unit_type=currency|asset` и `unit`
фильтруют серии точным совпадением. `limit` от 1 до 100 и `offset` пагинируют
series после устойчивой сортировки `metric, unit_type, unit`; buckets одной серии
возвращаются целиком в возрастающем порядке.

Для frontend identity использовать `series[].key`, а не label или позицию массива.
`series_total` считается после filters и до pagination. Пустая выборка возвращает
`series=[]`; historical reporting conversion остаётся явно `unavailable` и не
должна достраиваться текущим FX. В Swagger overview содержит synthetic
complete/partial/empty response examples, включая явный `null` недоступной метрики.

### API возвращает 422 для decimal

Money, Quantity, Price и Rate передаются JSON-строками без exponent notation и
разделителей групп:

```json
{"quantity":"10","price":"125.50","price_currency":"USD"}
```

JSON numbers вроде `125.50` намеренно отклоняются, чтобы не потерять точность.

### Frontend не получает API данные

Проверить, что API и Vite доступны, затем убедиться, что запросы browser идут на
`/api/v1`, а не на прямой database/storage endpoint:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health/ready
curl --fail --silent --show-error http://127.0.0.1:5173
```

Если изменился backend contract, запустить `npm run api:generate`, проверить diff
generated schema и адаптировать вызовы client. Не описывать новый response type
вручную и не компенсировать backend `null` frontend-нулём.

## Проверки разработки

```bash
uv run ruff check .
uv run mypy
uv run pytest -m "not postgres"
docker compose exec -T db createdb -U finance finance_regression_test  # только при первом запуске
FINANCE_DATABASE_URL=postgresql+psycopg://finance:finance@localhost:5432/finance_regression_test \
  uv run alembic upgrade head
FINANCE_TEST_DATABASE_URL=postgresql+psycopg://finance:finance@localhost:5432/finance_regression_test \
  uv run pytest -m postgres

cd frontend
npm run api:check
npm run format:check
npm run lint
npm run typecheck
npm run test
npm run build
npm audit
```

PostgreSQL-тесты используют только отдельную тестовую или локальную базу без
пользовательских данных.

Browser E2E автоматически поднимает отдельные API/worker/frontend на портах
`8011`/`5174` и использует только `finance_browser_test`. Regression API-тесты
используют `finance_regression_test`: браузерные записи не должны конфликтовать
с их фиксированными идентификаторами. E2E не обращается к базе и API
запущенного пользовательского приложения. Chromium устанавливается Playwright
отдельно от production dependencies:

```bash
cd frontend
npx playwright install chromium
npm run test:e2e
```

Сценарии используют только synthetic fixtures, проверяют persistent import review,
confirm/recalculation, overview/holdings, empty tablet state, keyboard focus и
отсутствие browser console errors. Coverage target намеренно не вводится.
