# Архитектура и стек

## Подход к первой фазе

Используется одна рабочая директория и небольшое число развёртываемых процессов. Доменные границы определяются сразу, но инфраструктура не усложняется преждевременно.

Локальный запуск:

```text
FastAPI API
Python worker
PostgreSQL
```

C++-модуль рассматривается после стабилизации расчётного контракта только при
подтверждённом профилированием ограничении Python-ядра.

## Стек

### Python

- современная поддерживаемая версия Python;
- FastAPI;
- Pydantic;
- SQLAlchemy 2.x async;
- Alembic;
- psycopg 3 или asyncpg;
- Polars для крупных CSV, стандартный `csv` для простых форматов;
- openpyxl для XLSX;
- defusedxml или эквивалентный безопасный streaming parser для XML;
- pypdf/pdfplumber только для адаптеров PDF;
- structlog для структурированных логов;
- pytest для ограниченного набора критичных тестов;
- Ruff;
- Pyright или mypy;
- uv для зависимостей.

### C++ при обоснованном переносе

- C++20 или C++23;
- CMake;
- pybind11;
- GoogleTest или Catch2 только для критичных вычислительных инвариантов.

### Хранилище и запуск

- PostgreSQL;
- локальное файловое хранилище за интерфейсом `ObjectStorage` в первой фазе;
- Docker Compose;
- Redis/RabbitMQ не добавлять, пока простой worker на PostgreSQL удовлетворяет требованиям.

## Доменные модули

```text
identity       пользователи и доступ
portfolios     портфели и базовая валюта
accounts       брокерские и CEX-счета; legacy bank type без продуктового развития
instruments    идентификаторы и свойства инструментов
operations     канонический ledger
imports        файлы, адаптеры, staging, preview и commit
pricing        ручные цены и валютные курсы
calculation    позиции, себестоимость и P&L
analytics      актуальная оценка, качество данных и read models
allocation     категории и фактические доли
shared         точные типы, время, ошибки, аудит
```

## Предлагаемая структура

```text
finance-aggregator/
├── apps/
│   ├── api/
│   └── worker/
├── src/
│   ├── identity/
│   ├── portfolios/
│   ├── accounts/
│   ├── instruments/
│   ├── operations/
│   ├── imports/
│   ├── pricing/
│   ├── calculation/
│   ├── analytics/
│   ├── allocation/
│   └── shared/
├── cpp/
│   ├── include/
│   ├── src/
│   ├── bindings/
│   └── tests/
├── migrations/
├── tests/
├── docs/
├── pyproject.toml
├── Dockerfile
└── docker-compose.yml
```

## Канонические операции

Не использовать одну перегруженную структуру для всех типов событий. Общие поля находятся в envelope, специфичные — в типизированном payload.

Типы первой версии:

- `trade`;
- `crypto_trade` — две instrument-ноги с точными количествами;
- `income`;
- `fee` — денежная комиссия либо точное количество instrument;
- `tax`;
- `cash_movement`;
- `currency_exchange`;
- `crypto_transfer`;
- `corporate_action`;
- `bond_redemption`;
- `balance_adjustment` — только с явным основанием.

Общий envelope:

```json
{
  "operation_id": "uuid",
  "portfolio_id": "uuid",
  "account_id": "uuid",
  "operation_type": "trade",
  "occurred_at": "2026-08-03T10:30:00Z",
  "source": {
    "type": "csv_import",
    "import_batch_id": "uuid",
    "source_operation_id": "external-id",
    "row_number": 42
  },
  "payload": {}
}
```

## Точность данных

- PostgreSQL `NUMERIC`, Python `Decimal`.
- Масштаб значения выбирается по типу поля, но исходная точность не теряется.
- JSON представляет точные числа строками.
- Валюта или идентификатор актива всегда хранится рядом с суммой.
- Округление задаётся политикой, а не происходит случайно при сериализации.
- Исходное значение строки импорта сохраняется отдельно от нормализованного.

## C++-ядро

C++ получает только нормализованные структуры и возвращает результаты расчёта:

```text
operations + prices + policy
    -> positions + cash balances + cost basis + P&L + diagnostics
```

C++ не отвечает за:

- HTTP;
- файлы;
- PostgreSQL;
- авторизацию;
- внешние API;
- бизнес-процесс подтверждения импорта.

Корректная Python-реализация является эталоном. После фиксации контрактов и
контрольных примеров вычислительное ядро можно перенести в C++ через pybind11,
если профиль целевого сценария показывает измеримую причину. Python- и
C++-реализации должны совпадать на полном результате эталонных и нагрузочных
сценариев. Контракт и текущая оценка описаны в `docs/CALCULATION_CORE.md`.

## Фоновые задачи

Для первой версии worker может получать задания из таблицы PostgreSQL через `FOR UPDATE SKIP LOCKED`.

Задачи:

- разбор импорта;
- валидация;
- подтверждение большого импорта;
- пересчёт портфеля;
- откат импорта.
- синхронизация публичного fiat FX без пользовательских данных.

Состояния задачи:

```text
pending -> running -> succeeded
                   -> failed
                   -> cancelled
```

## Наблюдаемость

Минимально необходимы:

- request/correlation ID;
- import batch ID в логах;
- структурированные ошибки адаптеров;
- продолжительность parse/validate/commit/recalculate;
- число строк ready/warning/error/duplicate;
- `/health/live` и `/health/ready`.

Персональные значения и полные строки файлов в логи не выводятся.

## Безопасность первой фазы

- Локальная разработка не должна маскироваться под production-безопасность.
- Исходные пользовательские отчёты не помещаются в рабочую директорию проекта.
- В fixtures нет настоящих ФИО, счетов и идентификаторов.
- Файлы проверяются по размеру, типу и допустимому расширению.
- Имя загруженного файла не используется как путь хранения.
- Парсер работает с ограничениями ресурсов.

## Реальные файловые адаптеры Phase 02

Первый набор реальных источников ограничен двумя брокерами:

```text
Т‑Инвестиции       official broker report XLSX
Альфа‑Инвестиции   official XML «для импорта»
```

Пользователь явно выбирает provider, после чего adapter обязан независимо
проверить внутреннюю сигнатуру документа. Код конкретного provider находится в
отдельном versioned adapter и не добавляется условными ветками в универсальный
CSV parser.

Позиции и сводные остатки из отчёта являются reconciliation data. Они не создают
вымышленные покупки. Денежные расчёты, НКД и комиссии, уже связанные с брокерской
сделкой, также не создаются повторно как независимые движения денег.

Официальные provider adapters могут автоматически создать только справочную
запись инструмента при однозначном ISIN, provider code или crypto asset code и
согласованных name/type/currency. Это происходит в транзакции staging resolution
до построения ledger candidate и не является финансовой операцией. Universal
fallback и противоречивые metadata сохраняют ручной instrument matching. Тип
provider проверяется против типа account (`broker` или `cex`) до сохранения batch.

## Pricing и FX в Phase 03

`market_prices` являются append-only observations с `source`, `provider`,
`price_kind`, `observed_at`, `fetched_at` и качеством времени. Публичная запись
остаётся только ручной; CoinPaprika adapter для проверенных криптоактивов
работает в worker, а российские бумаги остаются на ручных ценах (ADR-056).
Автоматическое наблюдение обязано ссылаться на глобальное
`market_mappings` с проверенными identifier, provider, market/board, symbol,
единицей, валютой и видом цены. `pricing` проверяет актуальное сопоставление и
при записи, и при выборе цены; отозванная или изменившаяся связь исключается
из новой оценки. Уникальность учитывает поставщика и вид цены, а расчётная позиция
хранит ID использованного observation. `exchange_rates`
хранят exact rate, направленную fiat pair, `observed_at`, `fetched_at`, источник и
mode. Выбор значения всегда ограничен `observed_at <= valuation_as_of`; возраст и
stale status являются частью публичного pricing contract.

Публичный CBR adapter находится за `FxRateProvider` и вызывается только worker’ом.
HTTP request не содержит portfolio, account, Ledger, import metadata или
credentials. Analytics endpoints читают только сохранённые observations и никогда
не выполняют сеть. PostgreSQL queue допускает import jobs с batch scope и
системные `fx_sync` и `market_data_sync` без batch; CHECK constraint запрещает
смешивать эти формы. Market-data worker включает общий CoinPaprika crypto snapshot
в рабочем режиме; в test mode live-реестр пуст. Искусственные проверки отдельно
подтверждают точность, повторы и выборочный пересчёт.

FX resolution ограничен direct, inverse и одним pivot через RUB. Одинаковые
currency не требуют FX; разные currency без курса остаются partial/unavailable и
не получают rate=1. Каждая денежная конвертация округляется half-even до 18 знаков
до агрегирования. `analytics.contracts` разделяет current value, basis, P&L,
income, fees, taxes, deposits и withdrawals и не позволяет missing component одной
метрики скрыто повредить другую.

## Allocation и current analytics в Phase 03

Семь глобальных system categories имеют устойчивые UUID; custom category всегда
принадлежит portfolio и сохраняет родительский system class. Override уникален по
portfolio+instrument и не меняет глобальный справочник инструментов. Cash balance
получает категорию cash напрямую, без synthetic instrument. Фактические веса
вычисляются по известной стоимости backend'ом; target plan выведен из MVP
решением ADR-044 и удалён forward-миграцией `0015_remove_allocation_targets`.

`analytics.service` является read-only application boundary. Она получает Ledger
count, один calculation snapshot, account/instrument metadata, allocation и
pricing через публичные Python services доменов и не сохраняет второй набор
агрегатов. Overview, holdings, пять breakdown dimensions, broker/CEX exposure и
allocation используют один known-value denominator в RUB либо USD. Неоценённая
компонента остаётся excluded; stale snapshot сохраняет known values только с
partial/unavailable quality и diagnostic. Legacy bank accounts в read model не
включаются.

## Ledger timelines и data quality в Phase 03

Исторические endpoint'ы читают подтверждённый Ledger через application service и
выполняют чистый operation-effects replay для per-operation basis/realised P&L.
Buckets строятся по локальным календарным границам IANA timezone, но события
остаются раздельными по исходной currency либо asset UUID. Current FX observations
не участвуют в исторической агрегации; отдельные timeline tables не создаются.

Публичная frontend-facing граница состоит только из отдельных Pydantic response
schemas; ORM records не возвращаются. Breakdown items имеют устойчивый `key`, а
timeline series — `key=<metric>:<unit_type>:<unit>`. Timeline принимает inclusive
`from`, exclusive `to`, bucket, canonical IANA timezone и exact `unit_type`/`unit`
filters. Диапазон ограничен 366 локальными календарными днями. Полный упорядоченный
набор series сортируется по metric, unit type и unit, после чего применяется
`limit<=100`/`offset`; buckets всегда возрастают и не пагинируются. Ответ сообщает
`series_total`, фактические `limit`/`offset` и возвращает пустой массив, если серия
не найдена.

`analytics.quality_service` переиспользует тот же current read model и получает от
imports только агрегированные counts. Public diagnostics содержат стабильные
code/severity/count и impacts, но не raw staging, account/instrument IDs,
контрольные суммы или финансовые значения. Provenance сообщает calculation
contract version, snapshot as-of/freshness и времена реально использованных
price/FX observations.
Phase 05 уточняет freshness: новое пригодное price observation делает сохранённую
оценку устаревшей до пересчёта. Analytics берёт происхождение цены из ссылки
позиции snapshot, поэтому не показывает новую котировку рядом со старой
стоимостью. Пересчёт загружает только по одной последней цене на инструмент и
валюту для инструментов данного портфеля; при равном времени ручная запись
выше автоматической.
Первый live provider — CoinPaprika Free: фиксированный общий crypto URL вызывается
только worker, 2 000 строк ответа фильтруются до пяти reviewed `coin_id`, а
сохраняются только цены использованных и точно сопоставленных инструментов.
`quotes.USD.price` разбирается как `Decimal`, `last_updated` становится временем
наблюдения; `fetched_at` не заменяет его для freshness. Worker coalesces общее
задание и пересчитывает затронутые портфели. Безопасная read-only диагностика
задания опубликована в `/api/v1/market-data/sync/latest`; browser видит
сохранённую оценку сразу и перечитывает snapshot без контакта с provider.
OpenAPI публикует complete, partial и empty synthetic overview examples. Обязательное
поле имеет JSON `null` только когда метрика недоступна; коллекции не заменяются
`null`, поэтому будущий frontend не должен угадывать форму пустого состояния.

## Граница frontend Phase 04

Первая frontend-версия является локальным HTTP client публичного `/api/v1` и не
получает прямого доступа к PostgreSQL, storage, import worker или Python services.
Backend остаётся владельцем Decimal-семантики, Ledger, reconciliation, valuation,
allocation и data-quality states. Browser отображает exact decimal strings,
stable keys, coverage и diagnostics без самостоятельной агрегации или скрытых
fallback.

Frontend изолирован в `frontend/` и собирается Vite 8 + React 19 + TypeScript 5.9
через npm lockfile. Route modules загружаются лениво. React Router владеет
устойчивыми portfolio/import URLs, TanStack Query — server state и его
инвалидацией, локальный React state — только временными form/view значениями.
Base UI используется для доступных primitives, React Virtuoso — для длинных
таблиц, Recharts — только для presentation геометрии уже рассчитанных backend
series. Численное преобразование для координат графика не участвует в формулах и
всегда сопровождается exact строковым табличным представлением.

`scripts/export_openapi.py` экспортирует актуальную схему в
`frontend/openapi.json`; `openapi-typescript` генерирует
`frontend/src/api/schema.d.ts`, а `openapi-fetch` типизирует запросы. Проверка
`npm run api:check` воспроизводит export/generation во временной директории и
отклоняет drift. Financial response types вручную не описываются.

В development Vite проксирует `/api` на `127.0.0.1:8000`; альтернативный origin
задаётся только через `VITE_API_BASE_URL`. Production bundle не содержит
`design-data.json` и synthetic суммы из эталонов. Общий API client распознаёт
публичный error envelope, а экраны различают loading, complete, partial,
unavailable, stale, empty и safe error. Подробный визуальный контракт фактической
реализации находится в `frontend/DESIGN.md`.

Для единого локального Compose-запуска frontend собирается multi-stage образом в
`frontend/Dockerfile`. Runtime image содержит только Node, `dist/` и небольшой
static server `frontend/scripts/serve.mjs`, без nginx и frontend dependencies.
Static server поддерживает SPA fallback и проксирует `/api`/`/health` во внутренний
Compose service `api:8000`. Наружу frontend и API публикуются раздельно на `5173`
и `8000`; это локальная эксплуатационная граница, а не production ingress.

## Планируемая граница read-only account connectors в Phase 06

Приватное подключение поставщика является transport пользовательской истории,
а не источником канонической цены. Оно привязано к внутреннему `account_id` и
через общий application interface получает executions, fees и asset/cash
movements. Публичный pricing worker продолжает независимо получать рыночные
observations. Account connector не передаёт pricing provider состав портфеля и
не использует личные исполнения как общую котировку.

Планируемый поток:

```text
read-only provider API -> sync run/raw provenance -> staging/normalization
                       -> preview/confirm -> canonical Ledger -> calculation

provider position/balance snapshot -> reconciliation diagnostics only

public market-data provider -> market_prices -> valuation
```

Initial API backfill проходит preview и подтверждение. После явного включения
connection точные incremental events могут подтверждаться автоматически;
ambiguous/unsupported события остаются в review. Cursor продвигается только
после атомарного сохранения страницы, overlap window и source ID обеспечивают
повторяемость. Исправления provider создают новую трассируемую версию, а не
молча изменяют подтверждённый Ledger.

Provider positions и balances являются контрольными totals. Расхождение не
создаёт автоматический adjustment: система показывает причину, расширяет
backfill либо требует review. Realised P&L рассчитывается финансовым ядром из
исполнений; provider aggregate «закрытая позиция» не становится Ledger event.

Credential доступен только через `CredentialStore`; секрет не возвращается в
API, не попадает в frontend/logs и не хранится в БД открытым текстом. Первый
срез остаётся локальным и однопользовательским. Один администраторский ключ для
обслуживания чужих портфелей, multi-user tenant isolation, торговля и вывод
средств архитектурно исключены. Полный контракт реализации находится в
`docs/PHASE_06.md`.
