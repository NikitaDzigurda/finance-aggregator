# Phase 03: агрегированная аналитика и allocation

## Цель

Превратить подтверждённый Ledger брокерских счетов и Bybit в объяснимый
аналитический backend, который отдаёт через Swagger готовые данные для будущего
frontend. Пользователь должен видеть актуальное состояние портфеля, структуру,
результаты, доходы, расходы, денежные события, фактические доли и качество данных.

Phase 03 использует ручные операции, ручные цены и файлы импорта пользовательских
данных. Приватные API брокеров/CEX не добавляются, но разрешён один системный
read-only FX gateway для публичных валютных курсов с ручным fallback. Деплой и
frontend не добавляются.

## Условия начала

Phase 02 завершена: Т‑Инвестиции XLSX, Альфа XML и Bybit Spot CSV bundle проходят
безопасный `upload -> preview -> confirm -> recalculate -> rollback`; calculation
contract v2 поддерживает две crypto asset-ноги и asset fee.

## Пользовательский результат

Через Swagger пользователь сможет:

1. добавить актуальные цены вручную и получить валютные курсы автоматически либо
   задать их вручную при недоступности источника;
2. пересчитать портфель на выбранный момент и получить результат в RUB или USD;
3. получить актуальную стоимость и составляющие результата;
4. посмотреть позиции, деньги и распределение по счетам, организациям, валютам,
   типам инструментов и категориям;
5. увидеть точные фактические веса категорий и явное покрытие оценки;
6. получить временные ряды событий Ledger: пополнения, выводы, income, комиссии,
   налоги, оборот и реализованный результат;
7. увидеть, какие части аналитики неполны из-за отсутствующей цены, курса,
   себестоимости или неполного импорта.

## Порядок работы

Работать сверху вниз. Не начинать оптимизацию запросов или расширенную метрику,
пока методика и правила неполных данных не закреплены эталонными примерами.
Отмечать `[x]` только после запуска кода, миграций и соразмерных риску проверок.

## 1. Зафиксировать методики и публичные термины

- [x] Определить `valuation_as_of`: цены и курсы берутся с максимальным
  `observed_at <= valuation_as_of`; будущие значения не используются.
- [x] Разрешить `reporting_currency=RUB|USD` во всех endpoint’ах актуальной
  стоимостной аналитики; `portfolio.base_currency` является значением по
  умолчанию, а не единственной доступной валютой вывода.
- [x] Разделить актуальную оценку, cost basis, realised P&L, unrealised P&L,
  income, fees, taxes, deposits и withdrawals; не называть пополнение прибылью.
- [x] Зафиксировать формулу каждой агрегированной метрики и порядок знаков.
- [x] Не складывать значения разных валют без явного курса в base currency.
- [x] Возвращать `total_value` только при полной оценке; при пропусках возвращать
  точный `known_value`, счётчики исключённых компонентов и diagnostic.
- [x] Определить состояния качества `complete`, `partial` и `unavailable` без
  непрозрачного рейтинга или скрытого заполнения данных.
- [x] Добавить ADR с методиками и совместимыми примерами JSON.

### Обязательные проверки

- [x] Deposit/withdrawal не входит в income или P&L.
- [x] Отсутствующий курс не заменяется единицей.
- [x] Неизвестная basis не заменяется нулём и блокирует только зависимую метрику.
- [x] Все точные значения в JSON остаются decimal strings.

Раздел завершён 2026-08-22. ADR-032 фиксирует cutoff `observed_at <=
valuation_as_of`, RUB/USD reporting contract, независимые метрики и sign convention.
`analytics.contracts` реализует общий partial-data contract: `total_value` доступен
только при полной оценке, `known_value` остаётся точным, а missing FX, price или
basis исключает только зависимую компоненту. Каждая FX-конвертация округляется
`ROUND_HALF_EVEN` до Money scale 18 до суммирования; JSON сохраняет decimal strings.

Эталонные проверки разделяют deposits, withdrawals, income и P&L, запрещают
неявный FX=1, сохраняют unknown basis и missing price как локальную недоступность и
подтверждают одинаковую консолидацию одного набора компонентов в RUB и USD.

## 2. Подготовить ручные цены и публичный FX gateway

- [x] Сохранить текущий append-only `market_prices`; задокументировать выбор
  последней цены на момент оценки и источник `manual`.
- [x] Добавить `exchange_rates` с точным rate, парой валют, `observed_at` и
  `fetched_at`, provider/source и mode `automatic|manual`; запретить одинаковые
  base/quote currencies.
- [x] Зафиксировать интерфейс `FxRateProvider`, не связанный с FastAPI, Ledger,
  import adapters или пользовательскими credentials.
- [x] Выбрать первичный публичный источник и при необходимости fallback; описать,
  является курс официальным дневным или рыночным и для какого времени он валиден.
- [x] Добавить `fx_sync` job в существующую PostgreSQL-очередь и отдельный Python
  worker handler; не выполнять сетевой запрос внутри analytics endpoint.
- [x] Настроить bounded timeout, retry с backoff, rate limit и безопасную
  diagnostic без сырого ответа внешнего сервиса.
- [x] Сделать сохранение observations идемпотентным по provider, валютной паре и
  времени курса; ошибочный ответ не заменяет последнее валидное значение.
- [x] Для версии 1 гарантировать консолидацию в двух reporting currencies: RUB и
  USD. Минимальная автоматически поддерживаемая пара — USD/RUB.
- [x] Для других fiat currencies разрешить direct, inverse либо один явно
  документированный pivot через RUB/USD; не выполнять произвольный FX graph search.
- [x] Добавить атомарные batch endpoint’ы для ручного ввода нескольких цен и
  курсов через Swagger как fallback без CSV market-data parser.
- [x] Возвращать возраст использованной цены/курса и отдельную stale diagnostic,
  не подменяя старое значение новым.
- [x] Не подключать автоматическую загрузку цен акций, облигаций и криптоактивов;
  исключение Phase 03 относится только к публичным fiat FX rates.

### Обязательные проверки

- [x] Значение после `valuation_as_of` не влияет на расчёт.
- [x] Direct и inverse FX дают одинаковый точный результат в допустимой шкале.
- [x] Один snapshot последовательно агрегируется в RUB и USD из одного набора
  price/FX observations без изменения Ledger и cost basis.
- [x] Повторный `fx_sync` не создаёт дубль.
- [x] Timeout, HTTP error, malformed payload и невозможный rate не повреждают
  последнее валидное наблюдение и не блокируют аналитику с явным stale status.
- [x] FX worker не читает portfolio, import raw rows или персональные данные.
- [x] Отсутствующая цена исключает только зависимую позицию.
- [x] Batch с одной неверной строкой не сохраняется частично.

Раздел завершён 2026-08-22. Миграция `0012_phase03_pricing_fx` добавила
append-only `exchange_rates`, source ручных prices и безопасный payload/scope для
`fx_sync` в существующей PostgreSQL-очереди. Она прошла `upgrade -> downgrade
0011 -> upgrade`; `alembic check` не обнаружил расхождений.

Primary provider — официальный дневной XML Банка России. Автоматическая пара
`USD/RUB` означает RUB за одну USD; дата действия переводится из полуночи
`Europe/Moscow` в UTC. HTTP используется только `FxRateProvider` внутри worker:
timeout, три попытки с bounded exponential backoff, лимит ответа, безопасная XML
валидация и минимальный интервал enqueue не раскрывают raw response. Последнее
валидное observation не заменяется при ошибке. Ручной fallback доступен через
атомарные `/api/v1/fx-rates/batch` и `/api/v1/prices/batch`.

`/api/v1/fx-rates/resolve` и `/api/v1/prices/resolve` выбирают только прошлое
observation, возвращают age и stale/missing diagnostics. FX resolution ограничен
direct, inverse и одним RUB pivot. Unit/OpenAPI проверки дали 12 passed, полный
быстрый набор — 78 passed/13 skipped, PostgreSQL — 13 passed. Реальный безопасный
`POST sync -> worker --once -> GET status/resolve` получил `fresh/direct` CBR
observation; созданные проверкой job и rate после проверки удалены.

## 3. Реализовать категории активов

- [x] Зафиксировать системные классы: `equity`, `fixed_income`, `fund`,
  `derivative`, `crypto`, `cash`, `other`.
- [x] Определить default mapping из существующего `instrument_type`; денежные
  остатки относятся к `cash` без создания вымышленного инструмента.
- [x] Добавить portfolio-specific категорию и необязательное переопределение
  категории конкретного инструмента.
- [x] Один инструмент внутри портфеля имеет не более одной активной allocation
  category; неразмеченный инструмент использует system default.
- [x] Сделать CRUD категорий и назначения инструментов через Swagger.
- [x] Не добавлять sector/country/issuer taxonomy без подтверждённых справочников;
  эти измерения остаются будущим расширением.

### Обязательные проверки

- [x] Stock, bond, fund, crypto asset и cash получают ожидаемый default class.
- [x] Portfolio override не изменяет глобальный instrument и другой portfolio.
- [x] Удаление используемой категории требует явного переназначения или отказа.

Раздел завершён 2026-08-22. Миграция `0013_allocation_categories` добавила семь
глобальных неизменяемых system categories, portfolio-specific custom categories и
ровно одно optional override на пару portfolio+instrument. Default mapping
детерминирован по `instrument_type`; денежный balance получает системный `cash`
на уровне аналитики без фиктивного инструмента. Custom category сохраняет связь с
system class, но sector/country/issuer не выводятся из неподтверждённых данных.

Swagger/API поддерживает list/create/update/delete categories и get/put/delete
instrument override. Category другого portfolio невидима; удаление system или
используемой custom category возвращает conflict до явного clear/reassignment.
Миграция прошла `upgrade -> downgrade 0012 -> upgrade`, `alembic check` не нашёл
расхождений. Ruff, mypy, OpenAPI smoke, unit mapping и PostgreSQL scope/delete
сценарий прошли.

## 4. Реализовать цели allocation — исторически завершено, затем superseded

Этот раздел фиксирует фактически выполненную 22.08.2026 работу. После Phase 04
пользователь уточнил границу MVP: управление инвестиционными целями не требуется.
ADR-044 supersede'ит действующий контракт этого раздела; endpoints, модели и
таблицы target plan удалены миграцией `0015_remove_allocation_targets`. Категории,
overrides, actual weights, denominator и partial-data semantics сохранены.

- [x] Добавить один активный target plan на portfolio с точными весами категорий.
- [x] Хранить веса в `NUMERIC` как доли от `0` до `1`, не в `float`.
- [x] Принимать полный набор целей атомарным `PUT`; сумма должна быть ровно `1`
  после документированной decimal normalization.
- [x] Разрешить цель для `cash` и пользовательских категорий.
- [x] Рассчитывать actual weight только по оценённым компонентам и явно указывать
  denominator/coverage при неполной оценке.
- [x] Возвращать target, actual и signed deviation; не рассчитывать сделки и не
  давать рекомендацию по ребалансировке.

### Обязательные проверки

- [x] Неверная сумма целей не оставляет частичную конфигурацию.
- [x] Нулевая стоимость портфеля возвращает недоступные веса, а не division error.
- [x] Missing price делает allocation partial и не перераспределяет пропуск между
  известными категориями без явного признака.
- [x] Сумма фактических долей по известному denominator точна в заданной шкале.

Раздел завершён 2026-08-22. Миграция `0014_allocation_targets` добавила один
target plan на portfolio и точные `NUMERIC(38,24)` weights. `PUT .../targets`
валидирует видимость всех категорий и exact Decimal sum `1` до изменения строк,
после чего заменяет полный набор одной транзакцией. Cash, system и custom
categories используют общий UUID contract; JSON numbers и неявное округление не
принимаются.

Actual allocation использует только holdings с известной текущей стоимостью и
возвращает reporting currency, known denominator, included/excluded counts и
quality. Missing holding не получает ноль; нулевой или отрицательный denominator
делает weights unavailable. Доли округляются half-even до 24 знаков, а остаток
назначается детерминированно крупнейшей известной категории, поэтому их сумма
ровно `1`. API возвращает target/actual/signed deviation без сделок и советов.
Unit и PostgreSQL проверки подтвердили точность, atomic invalid sum, custom/cash
targets, partial coverage и безопасный portfolio cascade.

Актуальный контракт после ADR-044 возвращает только category, known value и actual
weight. `target_weight`, `deviation` и `/allocation/targets` больше не являются
частью публичного `/api/v1`.

## 5. Построить актуальный аналитический read model

- [x] Добавить analytics application boundary, которая читает Ledger,
  calculation snapshot, pricing и allocation только через их прикладные
  интерфейсы.
- [x] Определять freshness snapshot относительно текущего Ledger; устаревший
  snapshot не выдавать как актуальный без diagnostic.
- [x] Реализовать overview: `as_of`, reporting currency, total/known value, cost basis,
  realised/unrealised components, income, fees, taxes и coverage.
- [x] Принимать `reporting_currency=RUB|USD` в overview, holdings, breakdown,
  exposure и allocation; ответ всегда сообщает фактически использованную валюту.
- [x] Реализовать holdings с account, institution, instrument, category,
  quantity, basis, market value, P&L и diagnostics.
- [x] Реализовать breakdown по account, institution, currency, instrument type и
  allocation category.
- [x] Реализовать counterparty exposure по broker/CEX institution без риск-оценки
  или инвестиционного вывода.
- [x] Не сохранять дублирующие агрегаты до подтверждённой проблемы скорости;
  источником истины остаются Ledger и актуальный calculation snapshot.

### Планируемые endpoint’ы

- `GET /api/v1/portfolios/{portfolio_id}/analytics/overview`
- `GET /api/v1/portfolios/{portfolio_id}/analytics/holdings`
- `GET /api/v1/portfolios/{portfolio_id}/analytics/breakdown`
- `GET /api/v1/portfolios/{portfolio_id}/analytics/exposure`
- `GET /api/v1/portfolios/{portfolio_id}/allocation`

### Обязательные проверки

- [x] Сумма оценённых holdings совпадает с `known_value` overview.
- [x] Breakdown по каждому измерению сходится с тем же denominator.
- [x] Broker и CEX accounts одновременно входят в один portfolio overview.
- [x] Пустой portfolio возвращает валидный нулевой результат.

Раздел завершён 2026-08-22. `analytics.service` является отдельной application
boundary и получает Ledger count, calculation snapshot, accounts/instruments,
allocation и pricing через их service interfaces. Новые таблицы агрегатов не
создаются. Freshness сравнивает operation count snapshot с текущим Ledger; при
расхождении known snapshot values сохраняются только как partial и сопровождаются
`calculation_snapshot_stale`, а `total_value` не выдаётся как актуальный.

Пять read-only endpoint'ов возвращают RUB/USD overview, instrument и cash
holdings, пять согласованных breakdown dimensions, broker/CEX counterparty
exposure и allocation comparison. Legacy bank accounts не входят в эти read
models. Missing price/FX/basis остаётся локальной unavailable/partial частью;
stale manual price/FX получает diagnostic. Пустой portfolio без Ledger корректно
возвращает нулевой overview и пустые projections.

Проверено одним PostgreSQL сценарием с broker+CEX: сумма известных holdings и
каждого breakdown равна overview `known_value`, RUB и USD используют один
snapshot, missing price не скрывается, actual weights дают exact sum `1`, новая
Ledger operation помечает snapshot stale, а все пять endpoint'ов принимают пустой
portfolio. Ruff, mypy и OpenAPI smoke прошли.

## 6. Добавить временные ряды событий Ledger

- [x] Не строить временной ряд цены инструмента или ежедневной стоимости
  портфеля без market data.
- [x] Добавить детерминированные per-operation calculation effects либо другой
  трассируемый derived contract для realised P&L и изменений basis.
- [x] Агрегировать по `day`, `week` и `month` пополнения, выводы, income, fees,
  taxes, trade turnover и доступный realised P&L.
- [x] Принимать `from`, `to`, `bucket` и IANA timezone; границы периода применять
  к timezone пользователя, операции хранить и сравнивать как aware datetime.
- [x] Возвращать события в исходной валюте или asset units. Не конвертировать
  прошлые события сегодняшним FX rate.
- [x] Не применять `reporting_currency` к историческому bucket без price/FX
  observation на дату события. В Phase 03 разноcurrency series возвращаются
  раздельно; единый RUB/USD history остаётся недоступным с явной diagnostic.
- [x] Использовать накопленные FX observations для исторического bucket только
  при полном покрытии его событий датированными курсами; неполный bucket не
  конвертировать частично под видом полного.
- [x] Сохранять ссылку агрегата на calculation version/as-of, чтобы frontend мог
  показать методику и свежесть.

### Планируемые endpoint’ы

- `GET /api/v1/portfolios/{portfolio_id}/analytics/cash-flows`
- `GET /api/v1/portfolios/{portfolio_id}/analytics/income`
- `GET /api/v1/portfolios/{portfolio_id}/analytics/costs`
- `GET /api/v1/portfolios/{portfolio_id}/analytics/trading`

### Обязательные проверки

- [x] Операция на границе дня попадает в правильный bucket выбранной timezone.
- [x] Fee и tax не дублируются между costs и total result.
- [x] Crypto transfer не считается trade turnover или realised P&L.
- [x] Overlap импортов не повторяет события временного ряда.
- [x] Текущий USD/RUB rate не меняет исторические buckets.

Раздел завершён 2026-08-22. Чистый operation-effects contract версии 1 повторяет
Ledger в том же устойчивом порядке, что calculation contract v2, и для каждой
операции возвращает точные изменения quantity/basis, доступный realised P&L и
локальные diagnostics. Эффекты не сохраняются отдельной таблицей: timeline
строится через application boundaries из неизменяемого Ledger, а provenance
возвращает обе версии контрактов, cutoff расчёта и состояние текущего snapshot.

Четыре endpoint'а принимают inclusive local `from`, exclusive local `to`,
`day|week|month` и каноническую IANA timezone. Пополнения, выводы, income,
денежные/asset fees, taxes, обычный и crypto turnover и доступный realised P&L
остаются раздельными currency/asset series. `reporting_currency` отсутствует;
ответ явно помечает unified historical conversion недоступной, поэтому сохранение
текущего USD/RUB observation не изменяет прошлые buckets. Unit/OpenAPI проверки и
два PostgreSQL-сценария подтвердили exact basis/P&L, московскую границу суток,
раздельные costs, исключение crypto transfer и отсутствие повторов после overlap
Т-Банк import.

## 7. Объединить качество и объяснимость данных

- [x] Собрать безопасный data-quality response из import completeness,
  reconciliation, unresolved review, calculation diagnostics, missing/stale
  prices, missing FX и unknown basis.
- [x] Использовать стабильные code/severity/count; не включать raw строки,
  пользовательские instrument identifiers, UID, адреса и финансовые значения.
- [x] Показывать влияние diagnostic: какие endpoint/metric являются partial или
  unavailable, а какие остаются корректными.
- [x] Добавить provenance: calculation contract version, snapshot as-of и время
  использованных price/FX observations.

### Планируемый endpoint

- `GET /api/v1/portfolios/{portfolio_id}/analytics/data-quality`

### Обязательные проверки

- [x] Missing price виден одновременно в holding, overview и data quality.
- [x] Reconciliation mismatch не раскрывает контрольные суммы или raw values.
- [x] Diagnostic одного счёта не делает недоступной независимую метрику другого.

Раздел завершён 2026-08-22. `GET .../analytics/data-quality` переиспользует
current analytics read model и получает import quality только через безопасный
application summary. Ответ агрегирует period/unknown completeness, reconciliation
mismatch, unresolved review, calculation, missing/stale price/FX и unknown basis
в стабильные `code/severity/count`; raw rows, batch/account/instrument identifiers,
UID, адреса, контрольные суммы и денежные значения не публикуются.

Каждая diagnostic возвращает список endpoint/metric impacts с фактическим
`complete|partial|unavailable`, поэтому локальная missing price не делает cost
basis или income недоступными. Provenance содержит calculation contract version,
snapshot as-of/freshness и времена реально использованных market price/FX
observations без их значений и asset identifiers. PostgreSQL-сценарии подтвердили
согласованность missing price между holding/overview/data-quality, безопасную
reconciliation diagnostic, unresolved overlap review и независимость известных
метрик. Ruff, mypy и OpenAPI проверки прошли; миграция БД не потребовалась.

## 8. Подготовить контракт для будущего frontend

- [x] Использовать отдельные Pydantic response schemas; ORM-модели наружу не
  отдавать.
- [x] Зафиксировать filters, sorting, pagination и максимальный диапазон для
  event endpoints.
- [x] Для breakdown/timeline возвращать стабильные keys и пустые массивы вместо
  неоднозначных `null`; `null` использовать только для недоступной метрики.
- [x] Добавить OpenAPI descriptions и synthetic examples для complete/partial/
  empty portfolio.
- [x] Проверить весь пользовательский сценарий только через Swagger.
- [x] Обновить `README.md`, `ARCHITECTURE.md`, `RUNBOOK.md`, `LIMITATIONS.md` и
  `DECISIONS.md` по фактически реализованному контракту.

Раздел завершён 2026-08-22. Все analytics/allocation routes используют отдельные
Pydantic response models; OpenAPI 200 responses ссылаются на их components, а ORM
records остаются внутри application services. Existing breakdown keys дополнены
timeline `series.key=<metric>:<unit_type>:<unit>`. Коллекции во всех пустых
состояниях являются массивами; `null` сохраняется только для действительно
недоступной метрики.

Четыре event endpoint'а ограничены 366 локальными днями, принимают exact
`unit_type`/`unit` filters и `limit<=100`/`offset` для series. До pagination series
детерминированно сортируются по metric/unit type/unit, buckets — по возрастанию;
ответ содержит `series_total`, applied limit и offset. OpenAPI описывает параметры
и содержит synthetic complete/partial/empty overview examples, включая required
`total_value: null` в partial state. Swagger/API smoke и PostgreSQL timeline
сценарий подтвердили schema refs, filters, pagination, stable keys, пустой ответ,
range rejection и существующую финансовую агрегацию. Решение закреплено ADR-039.

## 9. Завершение Phase 03

- [x] Запустить Ruff, mypy и только риск-ориентированные тесты этой фазы.
- [x] Проверить Alembic `upgrade -> downgrade -> upgrade` и `alembic check`.
- [x] Проверить смешанный synthetic portfolio: Т‑Инвестиции + Альфа + Bybit.
- [x] Проверить current valuation, allocation, event timelines и data quality
  одним-двумя сквозными API-сценариями.
- [x] Проверить, что внешние запросы ограничены разрешённым публичным FX provider,
  не используют credentials и не передают персональные/портфельные данные.
- [x] Измерить analytics endpoints на существующем synthetic benchmark только
  после корректности; оптимизировать лишь подтверждённое узкое место.
- [x] Зафиксировать следующий приоритет: frontend либо отдельная Market Data
  phase по новому решению пользователя.

Phase 03 завершена 2026-08-22. Ruff прошёл, mypy не обнаружил ошибок в 82 source
files. Риск-ориентированный unit/OpenAPI набор дал 40 passed, PostgreSQL-набор —
9 passed. Последний включает provider workflows с rollback и единый mixed
Swagger/API scenario: два broker accounts с Т‑Инвестициями и Альфой плюс Bybit CEX
создали 12 подтверждённых Ledger operations, после чего current valuation,
holdings, exact allocation, четыре event timelines и data quality остались
согласованными.

Временная изолированная PostgreSQL database прошла полный upgrade до
`0014_allocation_targets`, downgrade Phase 03 до `0011_bybit_spot_bundle` и
повторный upgrade; `alembic check` не нашёл новых операций, current — head. После
проверки временная БД удалена, существующие локальные данные не затрагивались.
Clean Docker build запустил healthy PostgreSQL/API и worker; liveness, readiness,
Swagger и OpenAPI вернули успешный ответ.

Network-аудит обнаружил единственный прикладной HTTP client в
`pricing.fx`: фиксированный HTTPS endpoint официального дневного XML CBR. Его job
содержит только системную пару USD/RUB, не использует credentials и не читает
portfolio/account/import payload. Поиск secrets и персональных отчётов обнаружил
только документированные synthetic fixtures и описания policy; XLSX отдельно
проверен на email, passport/private-key/secret markers без вывода содержимого.

После correctness gate семь измерений каждого endpoint на mixed synthetic
portfolio дали локальные медианы: overview 16,002 ms, allocation 15,959 ms,
trading 7,624 ms и data quality 15,157 ms. Это не SLA; подтверждённого узкого
места нет, поэтому оптимизация и новые aggregate tables не добавлялись.

Пользователь выбрал frontend как следующий приоритет. Решение зафиксировано в
ADR-040, исполняемый план — `PHASE_04.md`. Точный следующий шаг: Phase 04, раздел
1 «Зафиксировать frontend v1 contract и toolchain».

## Не входит в Phase 03

- frontend и дизайн интерфейса;
- деплой, multi-user identity, authentication и authorization;
- API брокеров/CEX для импорта пользовательских данных и хранение credentials;
- публичные API и workers цен акций, облигаций и криптоактивов; разрешён только
  системный read-only fiat FX gateway;
- исторические цены, daily portfolio value и график цены инструмента;
- TWR, XIRR, Sharpe, Sortino, beta, volatility и drawdown;
- автоматическая ребалансировка, создание сделок и инвестиционные рекомендации;
- банковские счета, карты, вклады, кошельки и DeFi;
- полный test coverage и тестирование тривиального wiring.

## Критерий готовности

Phase 03 завершена, когда после ручного импорта, ручного ввода актуальных prices и
автоматического либо fallback-ручного получения FX Swagger возвращает
согласованный overview, holdings, breakdowns, actual allocation, event
timelines и data-quality diagnostics. Frontend не должен самостоятельно
пересчитывать финансовые показатели или догадываться о неполных данных.
