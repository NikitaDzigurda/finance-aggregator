# Phase 04: первая локальная frontend-версия

## Цель

Добавить локальный web frontend поверх завершённого backend `/api/v1`. Пользователь
должен пройти импорт, ввести необходимые prices/FX, увидеть актуальную аналитику,
allocation, события Ledger и качество данных без Swagger и без повторения
финансовых вычислений в browser.

Phase 04 не меняет Ledger model, import adapters или calculation methodology.
Backend остаётся источником exact decimal strings, stable keys, quality states,
diagnostics и provenance.

## Условия начала

Phase 03 завершена: mixed Т‑Инвестиции + Альфа + Bybit portfolio проходит
`upload -> preview -> review -> confirm -> recalculate`, а `/api/v1` возвращает
current valuation, holdings, breakdown, exposure, allocation, bounded event
timelines и safe data quality через отдельные Pydantic schemas.

## Порядок работы

Работать сверху вниз. Не начинать визуальную полировку до стабильного typed API
client, общих complete/partial/empty states и одного рабочего end-to-end сценария.
Не добавлять вычисления денег, weights или P&L на frontend. Отмечать `[x]` только
после запуска и соразмерной проверки.

Работа делится на два поставляемых среза. Slice A даёт полный путь
`portfolio -> account -> import -> data readiness -> recalculate -> overview`.
Slice B добавляет allocation, расширенные breakdown/exposure и Ledger timelines.
Оба среза входят в Phase 04, но первый обязан оставаться рабочим независимо от
готовности второго. UX/UI-контракт находится в `docs/UX_UI_BRIEF_V1.md`.

## Утверждённый визуальный контракт

Пользователь утвердил refined frontend v1 direction от 22.08.2026. Источником
визуальной правды для реализации являются:

- `docs/design-concepts/frontend-v1/DESIGN_DIRECTION_FRONTEND_V1.md` — visual
  system, composition и responsive rules;
- `01-overview-desktop.png` — overview;
- `02-holdings-desktop.png` — assets/holdings;
- `03-import-start-desktop.png` и `04-import-review-desktop.png` — import flow;
- `05-data-quality-desktop.png` — data readiness и corrective actions;
- `06-allocation-desktop.png` — верхний блок actual allocation; нижний target
  editor отменён решением ADR-044;
- `07-events-desktop.png` — Ledger timelines;
- `08-overview-tablet.png` — tablet recomposition overview;
- `OPENAPI_UI_CHECK.md` — карта экранов на public `/api/v1`;
- `design-data.json` — только synthetic visual fixture, не runtime data source и
  не замена generated OpenAPI types.

Реализация должна сохранять информационную иерархию, плотность, состояния и
responsive composition макетов, но не копировать демонстрационные значения в
production UI. Если изображение расходится с актуальным OpenAPI, финансовая
семантика и public contract backend имеют приоритет; визуальное расхождение надо
зафиксировать в документации, а не скрывать frontend-вычислением.

## 1. Зафиксировать frontend v1 contract и toolchain

- [x] Согласовать sitemap, основной user flow и макеты slice A по
  `docs/UX_UI_BRIEF_V1.md`; зафиксировать выбранную визуальную систему отдельно от
  продуктовой спецификации в `docs/design-concepts/frontend-v1/`.
- [x] Выбрать и закрепить TypeScript web toolchain, package manager, lint,
  formatting и test commands без floating dependency versions.
- [x] Разместить frontend в отдельной workspace boundary без смешивания с Python
  domain packages.
- [x] Зафиксировать browser API base URL, local development proxy/CORS и единый
  error-envelope client для `/api/v1`.
- [x] Получать TypeScript API types из OpenAPI либо проверяемого generated client;
  не создавать вручную расходящиеся financial response types.
- [x] Сохранять decimal values строками; запрещать преобразование money, quantity,
  price, rate и weight в binary `number` для расчётов.
- [x] Определить общие UI states: loading, complete, partial, unavailable, empty и
  safe error без raw import payload.
- [x] Разделить разрешённую presentation/form validation и запрещённые финансовые
  вычисления: exact decimal library допустима для форматирования и проверки суммы
  target input, но не для пересчёта backend analytics.
- [x] Зафиксировать минимальные browser support и accessibility правила.
- [x] Добавить минимальный `GET /api/v1/imports` list contract с фильтром по
  portfolio/account/status, чтобы UI мог восстановить активный импорт и показать
  историю после reload; не читать import tables напрямую.
- [x] Добавить ADR, README и локальные команды запуска до feature screens.
- [x] Создать frontend `DESIGN.md` с фактически реализованными tokens,
  typography, spacing, primitives и responsive rules; исходное направление не
  переписывать под ограничения первой реализации без явной записи причины.

### Обязательные проверки

- [x] Frontend dev server обращается только к документированному `/api/v1`.
- [x] Generated types включают required `null` для unavailable metric и массивы
  для empty collections.
- [x] Decimal contract не теряет точность на длинном synthetic значении.
- [x] OpenAPI drift check обнаруживает несовместимое изменение generated client.
- [x] Backend Ruff/mypy и существующие risk tests не повреждены scaffold'ом.

## 2. Реализовать app shell, onboarding и справочные данные

- [x] Добавить layout, navigation и явный текущий portfolio context по app shell
  утверждённых desktop/tablet макетов.
- [x] Реализовать list/create portfolio и broker/CEX accounts без типа bank в
  пользовательском flow.
- [x] Реализовать необходимый для импорта справочник instruments: list/search,
  create и переход к созданию из unresolved import row с последующим match.
- [x] Добавить first-run маршрут `portfolio -> account -> import`, а не вести
  нового пользователя на пустой аналитический dashboard.
- [x] Добавить route-level loading/error/empty states и устойчивые URLs.
- [x] Не добавлять multi-user identity или authentication в локальную версию.

Дополнено 2026-08-28: в app shell добавлено явное действие «Новый портфель» рядом
с выбором текущего портфеля. Оно открывает существующий typed onboarding flow и
не создаёт фиктивный пользовательский профиль или отдельный frontend-контракт.
Повторный onboarding отличает дополнительный портфель от первого запуска.

## 3. Реализовать import workflow

- [x] Показать поддерживаемые provider formats из `/import-formats`.
- [x] Показать список import batch и позволить восстановить processing/review flow
  после reload по устойчивому URL.
- [x] Реализовать multi-file upload для Bybit и single-file XLSX/XML uploads.
- [x] Для Bybit показать checklist четырёх обязательных документов до upload.
- [x] Показать status, safe summary, diagnostics и reconciliation без production
  logging raw rows.
- [x] Реализовать preview/review/exclude/match/confirm/recalculate/rollback с
  явными destructive confirmations там, где нужны.
- [x] Использовать bounded polling import/job status с остановкой в terminal state;
  не создавать бесконечный browser loop.
- [x] Сохранять idempotent duplicate/confirm/rollback states понятными пользователю.
- [x] Сопоставить UI states с `03-import-start-desktop.png` и
  `04-import-review-desktop.png`, не превращая семь визуальных шагов wizard в
  новые backend entities или недокументированные статусы.

## 4. Реализовать current analytics dashboard

- [x] Показать overview в RUB/USD с quality, coverage и snapshot freshness.
- [x] Показать holdings, breakdown и broker/CEX exposure по stable keys.
- [x] Не суммировать значения и не подменять `null` на zero в browser.
- [x] Добавить data-readiness panel: missing/stale price/FX, unknown basis,
  incomplete import и stale snapshot с доступным следующим действием.
- [x] Реализовать batch-ввод ручных prices, запуск/status CBR FX sync, ручной FX
  fallback и повторный recalculation.
- [x] Не показывать график рыночной стоимости или доходности портфеля: backend не
  предоставляет daily valuation series.
- [x] Сопоставить overview, holdings и readiness с
  `01-overview-desktop.png`, `02-holdings-desktop.png` и
  `05-data-quality-desktop.png`; overview не должен становиться полной ведомостью.

## 5. Реализовать allocation

- [x] Показать system/custom categories и instrument overrides.
- [x] Показать фактическую стоимость, actual weight, denominator и partial
  coverage без frontend-пересчёта.
- [x] Не рисовать круговую диаграмму при отрицательном cash/заёмной позиции:
  сохранять точные знаковые суммы и backend weights в таблице и показывать
  понятное ограничение визуализации.
- [x] Удалить выведенные из MVP target plan, target/deviation и редактор целей;
  сохранить категории, overrides и фактическую allocation-аналитику.

## 6. Реализовать timelines и data quality

- [x] Показать cash-flow, income, costs и trading как вкладки одной событийной
  поверхности с range не более 366 дней.
- [x] Использовать `series.key`, filters, `series_total` и series pagination.
- [x] Не объединять currency/asset series и не конвертировать прошлое текущим FX.
- [x] Для каждого графика предоставить табличное представление тех же buckets.
- [x] Показать data-quality code/severity/count, impacts и provenance без raw data.

## 7. Проверить основной пользовательский сценарий

- [x] Пройти synthetic Tbank, Alfa и Bybit imports из browser UI.
- [x] Проверить complete, partial и empty portfolio states.
- [x] Проверить один responsive desktop/tablet flow и keyboard navigation.
- [x] Проверить persistent import/error states после browser reload.
- [x] Добавить один-два browser end-to-end сценария только для финансово и
  импортно критичного пути; не вводить coverage target.
- [x] Снять screenshots всех семи desktop screens и overview tablet, сравнить с
  утверждёнными reference images и исправить существенные расхождения в
  hierarchy, spacing, typography, states и responsive composition.
- [x] Проверить visual states на данных public API, отдельно включая `complete`,
  `partial`, `unavailable`, `empty`, длинные Decimal strings и русские подписи;
  `design-data.json` использовать только как synthetic fixture.

## 8. Завершение Phase 04

- [x] Запустить frontend lint/typecheck/build и риск-ориентированные tests.
- [x] Повторно запустить backend Ruff/mypy и затронутые API tests.
- [x] Проверить clean local startup backend + frontend без external deployment.
- [x] Проверить отсутствие credentials, raw financial logs и real reports.
- [x] Обновить README, ARCHITECTURE, RUNBOOK, LIMITATIONS и DECISIONS.
- [x] Зафиксировать следующий приоритет отдельным решением пользователя:
  Phase 05 — модуль рыночных данных (ADR-051, `docs/PHASE_05.md`).

## 9. Укрепить официальный ручной импорт по реальным структурам

- [x] Добавить актуальную много-листовую XLSX-компоновку Т‑Инвестиций, сохранив
  совместимость с историческим листом `broker_rep`.
- [x] Принимать фактический datetime Альфа‑Инвестиций и автоматически отделять
  ledger effects от trade-linked и reconciliation-only строк.
- [x] Распознавать фактические singular/plural названия классов инструментов
  Альфа‑Инвестиций (`Акция`, `Биржевой фонд` и другие наблюдённые варианты), чтобы
  перенос бумаги автоматически создавал однозначный instrument reference.
- [x] Разрешать same-second executions и секундное расхождение внутренних
  переводов Bybit по точным provider-полям.
- [x] Нормализовать наблюдавшиеся Bybit Airdrop, Fiat/P2P и IPO как quantity
  adjustments без выдумывания valuation и P&L.
- [x] Автоматически создавать справочный instrument только для trusted adapter с
  однозначным identifier и согласованными metadata.
- [x] Проверять соответствие broker/CEX provider типу выбранного account.
- [x] Убрать из обычного frontend review raw staging, готовые строки и технические
  исключения; показывать только требующие решения warning/error/duplicate.
- [x] Проверить parser compatibility на исходных файлах вне repository без их
  копирования, логирования персональных значений или замены synthetic fixtures.
- [x] Запустить frontend lint/typecheck/build и профильные unit checks.
- [x] Повторно пройти PostgreSQL import workflow и browser smoke после запуска
  локального Docker daemon.
- [x] Изолировать PostgreSQL и browser E2E в `finance_test`; не создавать
  synthetic QA-портфели и инструменты в базе запущенного приложения.
- [x] Отличать обычный Alfa XML `MyBroker` от поддерживаемого XML «для импорта» и
  показывать пользователю точную инструкцию вместо generic adapter error.
- [x] Перезапускать локальный worker после неожиданного завершения и заменять
  бесконечный processing state диагностикой задержавшегося задания.

## 10. Исправления ручной проверки, 12.09.2026

- [x] Добавить автоматический публичный USD/RUB sync в работающий worker и
  защитить конкурентную постановку advisory lock; сохранить ручной fallback.
- [x] Показывать последнее сохранённое FX-задание после перезагрузки страницы
  через `GET /api/v1/fx-rates/sync`, текущий курс и его дату.
- [x] Ограничить выбор цены инструментами текущего портфеля, подставлять валюту
  выбранной позиции и пересчитывать после сохранения цены/курса.
- [x] Исправить форму broker/CEX account, подсказку блокировки upload, long filenames,
  сетку импорта и отступ диаграммы от таблицы.
- [x] Создать отдельный extensive manual QA bundle: 15 + 15 брокерских сделок,
  12 Bybit executions, согласованные остатки, expected.json и инструкция.
- [x] Подготовить отдельную папку демо-портфеля на рабочем столе по запросу
  пользователя: шесть совместимых файлов, инструкция, контрольные значения и
  результат проверки. 12.09.2026 повторно проверены detect/parse/validate,
  брокерская reconciliation, остатки через Decimal и SHA-256 скопированных файлов;
  рабочая БД не изменялась. Восстановлена инструкция `fixtures/manual_qa/README.md`.
- [x] Проверить upload/confirm/recalculate/dedup/rollback этих файлов через API
  в отдельной regression database; не менять старые negative fixtures.
- [x] Устранить случайный порядок связанных Bybit execution/asset fee при равном
  timestamp в snapshot и operation effects. Проверить отсутствие ложного
  `negative_position`; неизвестную реальную себестоимость не заполнять догадками.
- [x] Разделить `finance_regression_test` и `finance_browser_test`, сохранив
  существующие локальные базы и пользовательские записи без удаления.
- [x] Проверить локальный Docker startup и успешный автоматический CBR job.
- [x] Завершить проверки: 106 backend tests, 4 browser E2E; typecheck, ESLint,
  Ruff, mypy и generated OpenAPI check. Проверить форму импорта на ширинах
  1440/1024/390, исправить перекрытие мобильной шапки и контраст названий файлов.

Остаётся продуктовым ограничением: нет автоматических котировок инструментов;
ограниченный отчёт или Fiat/P2P без фиатной суммы не гарантирует полную
историческую себестоимость. Статусы partial/unavailable не скрываются.

### Проверка P&L демо-портфеля через браузер, 12.09.2026

- [x] Воспроизвести отсутствие нереализованного P&L через frontend и ручной
  GET positions в Swagger: у десяти брокерских акций basis известна, отсутствуют
  рыночные цены; неизвестная basis относится только к трём Bybit assets.
- [x] Исправить data-quality summary: повторные operation diagnostics считать
  один раз на account/instrument/code/severity. Убрать ошибочную зависимость
  basis от market price и income от basis; указать влияние на unrealised P&L.
- [x] Добавить объяснение необходимости цен в обзоре и диагностике;
  после пересчёта обзора обновлять также holdings, allocation и quality cache.
- [x] Ввести контрольные цены демо-сценария через browser UI и проверить
  автоматический пересчёт. Не изменять исходные операции или неизвестную basis.
- [x] Проверить regression для счётчика и связей метрик, financial contracts и
  полный manual-QA API workflow в `finance_regression_test`: 5 passed.
  Ruff, mypy, frontend typecheck, ESLint и production build прошли.
- [x] После пересборки локального Compose повторно проверить browser UI:
  предупреждение basis содержит 3 позиции вместо 13 событий, missing prices
  отсутствует, брокерский realised P&L и unrealised P&L совпадают с контрольным
  сценарием. Неизвестная basis Bybit остаётся явной partial diagnostic.

### Полная синтетическая история Bybit

- [x] Добавить самостоятельный комплект `fixtures/bybit_complete`: четыре CSV
  без повторного P2P adjustment, две операции приобретения в `acquisition.json`,
  контрольные цены/значения и загрузчик через публичный API в новый CEX-портфель.
- [x] Проверить известную basis и complete P&L, количества независимой рациональной
  арифметикой, повторные upload/confirm. Новый сквозной тест и прежний manual-QA
  workflow прошли на временной изолированной PostgreSQL: 2 passed; Ruff и mypy прошли.
- [x] Исправить устаревшие ORM collections в ответе повторного POST recalculate:
  после сохранения явно обновлять positions/cash_balances/currency_metrics.
  Сквозная проверка подтвердила совпадение результата POST с последующим GET.

### Локальный запуск и очистка 2026-09-12

- [x] Добавить `restart: unless-stopped` для db/api/frontend, как у worker;
  применить политику к работающим контейнерам без пересборки. Проверить Compose
  config, HTTP 200 для frontend/assets/API proxy и открытие обзора в браузере
  по localhost и 127.0.0.1. На момент диагностики сбой открытия не воспроизводился;
  прежняя причина не установлена. Перезапуск самого Docker не выполнялся.
- [x] Удалить остановленную временную PostgreSQL `bybit-complete-pg.tSfA89`,
  промежуточный пакет демо, упаковочный скрипт и кэши проверок. Очистить
  неиспользуемый Docker build cache; рабочие контейнеры, тома и готовые
  демонстрационные отчёты сохранены.
- [x] Подготовить `docs/ПОЛНОЕ_ОПИСАНИЕ_ПРОЕКТА.docx`: 12 страниц об идее,
  пользовательском пути, архитектуре, импорте, расчётах и ограничениях с
  восемью подписанными снимками экранов. Итоговая версия отрисована и постранично
  проверена; иллюстрации используют только искусственный демо-портфель.
- [x] Зафиксировать результаты обсуждения присланных экранов в
  `docs/FRONTEND_UX_IMPROVEMENT_PLAN.md`: проблемы, предлагаемые решения,
  критерии готовности, приоритеты и продуктовые вопросы. Изменения интерфейса
  этим пунктом не считаются реализованными.

### Подготовительное исправление формулировок 2026-09-12

- [x] Заменить вводящую в заблуждение подпись об «актуальном snapshot» на
  описание расчёта по сохранённым операциям, ценам и курсам; рядом показывать
  дату и отдельное состояние устаревшего расчёта или входных данных.
- [x] Явно описать ручное сохранение цен инструментов и автоматический характер
  только поддерживаемого курса USD/RUB Банка России.
- [x] Показывать себестоимость и нереализованный P&L денежных средств как
  неприменимые без изменения серверных вычислений.
- [x] Перевести основные diagnostics, влияния и счётчики качества; технические
  коды перенести в раскрываемые подробности.
- [x] Заменить английское предупреждение исторической конвертации русским
  объяснением исходных валют и не выдавать события за историю стоимости.
- [x] Проверить production bundle, typecheck, ESLint, unit-тесты и локальный
  browser UI на полном и частичном расчёте.

Автоматические котировки инструментов, внешние market-data API, фоновые задания
цен и остальные улучшения интерфейса из отдельного плана в этот этап не входят.

### Последующая правка отображения 2026-09-12

- [x] Удалить раскрываемые технические подробности с экранов качества данных и
  событий и нижнюю строку-подсказку экрана качества данных.
- [x] Перевести основные пользовательские подписи без изменения контрактных
  значений API и сохранённых финансовых данных.
- [x] Заменить значок вкладки круглым знаком приложения и согласовать цвета
  начального экрана с основной тёмно-сине-бирюзовой палитрой.

## Не входит в Phase 04

- production deployment, TLS, multi-user auth и authorization;
- private broker/CEX APIs и хранение provider credentials;
- автоматические цены инструментов и отдельная Market Data phase;
- trading, withdrawals, rebalancing orders и investment recommendations;
- mobile application, банковские счета, карты, вклады, wallets и DeFi;
- ручной UI для всех вариантов canonical Ledger operation и correction workflow;
- график ежедневной стоимости портфеля, исторические котировки и performance
  metrics, которых нет в backend contract;
- обязательные light+dark themes и декоративная анимация как отдельный scope;
- frontend financial calculations, full test coverage и tests trivial rendering.

## Критерий готовности

Phase 04 завершена, когда локальный пользователь без Swagger проходит import и
review, пересчитывает portfolio и видит current analytics, allocation, timelines и
data-quality states. UI использует только public `/api/v1`, сохраняет Decimal/null/
empty semantics и не принимает финансовых решений вместо backend.
