# Phase 02: реальные брокерские отчёты и CEX

## Цель

Добавить ручной импорт официальных брокерских отчётов Т‑Инвестиций и
Альфа‑Инвестиций, а после стабилизации обоих адаптеров расширить канонический
контракт для CEX. Phase 02 остаётся backend-first: все сценарии доступны через
Swagger, frontend и реальные API не добавляются.

Обычные банковские счета, карты, вклады и история покупок исключены из scope.

## Порядок работы

Работать сверху вниз. Не начинать CEX, пока оба брокерских формата не проходят
полный безопасный сценарий импорта. Отмечать `[x]` только после реализации и
соразмерной риску проверки.

## 1. Зафиксировать входные форматы и границы полноты

- [x] Выбрать первые источники: Т‑Инвестиции и Альфа‑Инвестиции.
- [x] Выбрать основной формат Т‑Инвестиций: официальный XLSX broker report.
- [x] Выбрать основной формат Альфа‑Инвестиций: XML «для импорта» с корнем
  `report_broker`.
- [x] Проверить заполненный отчёт Альфы и связь сделок с денежными расчётами.
- [x] Добавить полностью искусственные structural fixtures обоих форматов.
- [x] Описать, какие секции создают ledger operations, а какие используются
  только для reconciliation или review.
- [x] Зафиксировать format ID, версию `1.0`, timezone `Europe/Moscow` и правило
  проверки совместимости перед изменением adapter.

### Результат

В project tree нет исходных пользовательских отчётов. Агент может изучить
структуру по `docs/fixtures/` и `docs/IMPORT_DESIGN.md`, не видя персональных
данных или финансового состояния пользователя.

## 2. Подготовить безопасный XML upload

- [x] Добавить `xml` в declared formats, allowlist расширений и content types.
- [x] Проверять XML signature после BOM и допустимого whitespace.
- [x] Запретить DTD, entity expansion, внешние сущности и сетевые обращения.
- [x] Ограничить размер, глубину, число элементов и длину текстовых значений.
- [x] Сохранить текущую потоковую запись, SHA-256 и повторную проверку файла worker.
- [x] Не включать текст XML, ФИО, договор, активы и суммы в production diagnostics.

### Обязательные проверки

- [x] Валидный synthetic Alfa XML проходит upload и попадает в parse job.
- [x] Подменённый HTML/XML, DTD/entity payload и слишком глубокий XML отклоняются.
- [x] Повторная загрузка того же файла на тот же account остаётся идемпотентной.

Раздел завершён 2026-08-10: `xml` добавлен в API, storage allowlist и DB constraint.
Upload до сохранения выполняет потоковую проверку Expat с запретом DTD, объявлений
entity и external entity resolution, не устанавливает сетевой resolver и ограничивает
25 MiB по общей настройке, глубину 64, 100 000 элементов, значение 65 536 символами
и число атрибутов одного элемента. UTF BOM и допустимый начальный whitespace
учитываются при проверке сигнатуры; XML с HTML root отклоняется.

После потокового сохранения прежними остаются server-generated storage key, SHA-256
и уникальность account+hash. Worker сначала повторно сверяет size/SHA-256, затем
заново применяет XML security policy. Public и worker diagnostics содержат только
стабильный code и безопасное сообщение без parser exception или содержимого XML.

Миграция `0008_xml_import_format` прошла `upgrade -> downgrade -> upgrade`, а
`alembic check` не обнаружил расхождений. Ruff и mypy прошли; 11 unit/security
тестов и 2 PostgreSQL import API-теста проверили synthetic Alfa upload, pending
parse job, extension/content type, BOM, malformed XML, HTML masquerading,
DTD/entity/external URL, depth/element/value limits, worker revalidation и
идемпотентный duplicate upload.

## 3. Реализовать Т‑Инвестиции XLSX adapter v1

Контракт: `format_id=tbank_broker_xlsx`, `version=1.0`.

- [x] Добавить отдельный adapter без provider-веток в universal CSV parser.
- [x] Detection: XLSX, лист `broker_rep`, заголовок отчёта и обязательные секции.
- [x] Разобрать период отчёта, исполненные сделки, комиссии и идентификаторы
  инструментов.
- [x] Нормализовать buy/sell по виду сделки и точным Decimal-полям.
- [x] Создавать fee отдельной staging row с общей ссылкой на исходную строку сделки.
- [x] Использовать ISIN, затем provider code для сопоставления инструмента.
- [x] Разобрать денежные остатки и движение бумаг как reconciliation data.
- [x] Пустые и неподдерживаемые секции отражать явной diagnostic.
- [x] Документировать ограничение synthetic fixture: она структурная и не заменяет
  регрессионную проверку на новых обезличенных версиях формата.

### Обязательные проверки

- [x] Detection не принимает произвольный XLSX или XLSX Альфы.
- [x] Покупка, продажа и комиссия создают точные раздельные candidates.
- [x] Повторяющийся номер сделки не создаёт дубль после confirm.
- [x] Позиции и остатки не превращаются в вымышленные операции.
- [x] Проходит один сквозной upload -> preview -> confirm -> recalculate -> rollback.

Раздел завершён 2026-08-10: зарегистрирован `tbank_broker_xlsx` версии `1.0` как
отдельный adapter на openpyxl. Detection проверяет bounded XLSX container, лист
`broker_rep`, период в заголовке, секции исполненных сделок, денег и движения бумаг,
а также обязательные trade columns. Период сохраняется в batch; неизвестные и
известные пустые секции дают безопасные diagnostics без текста строк.

Исполненная сделка нормализуется в точный trade с timezone `Europe/Moscow` и
внешним ID из номера сделки. Ненулевые комиссии агрегируются по валюте и становятся
отдельными fee rows с тем же source row и детерминированным derived external ID.
Instrument matching расширен provider code и сохраняет приоритет ISIN. Денежные
остатки и движения бумаг сохраняются отдельными excluded staging rows только для
reconciliation; candidates ledger для них не создаются. Python `None` для таких
строк закреплён как SQL NULL через `JSONB(none_as_null=True)`.

Ruff и mypy прошли. Четыре unit-теста проверили detection, период, buy/sell,
точные trade/fee, произвольный XLSX, empty/unsupported diagnostics и отсутствие
ledger candidates у reconciliation. Пять PostgreSQL import API/workflow тестов
прошли, включая полный Tbank `upload -> preview -> confirm -> recalculate ->
rollback`, повторный confirm/rollback, provider-code match и overlap report с тем
же trade number: второй batch получил duplicate rows, а ledger остался из двух
операций. `alembic check` не обнаружил изменений схемы.

Synthetic fixture описывает только наблюдаемую структуру и один искусственный
экономический сценарий. Она не является полной спецификацией provider и не заменяет
регрессионную проверку каждой новой обезличенной версии официального XLSX перед
расширением совместимости adapter `1.0`.

## 4. Реализовать Альфа‑Инвестиции XML import adapter v1

Контракт: `format_id=alfa_broker_xml_import`, `version=1.0`.

- [x] Detection: `report_broker` и обязательные metadata/collection elements.
- [x] Разобрать `trades_finished` в trade и отдельные fee candidates.
- [x] Определять buy/sell по знаку `qty`; `summ_trade` не использовать как знак.
- [x] Использовать `trade_no` как source operation ID в пределах account/provider.
- [x] Сопоставлять инструмент по `isin_reg`, затем по provider fields.
- [x] Не подтверждать `trades_unfinished`; показывать их как информационные строки
  preview с устойчивым кодом diagnostic.
- [x] Связать `money_moves.trd_no` с завершёнными сделками.
- [x] Не создавать второй cash movement для расчёта, НКД и комиссии той же сделки.
- [x] Несвязанные пополнения, выводы, купоны и банковские тарифы нормализовать в
  `cash_movement`, `income` и `fee`.
- [x] Использовать `positions` и `money_moves_total` для контрольных сверок.
- [x] Оставлять `transfers` на review до явной политики переноса себестоимости.
- [x] Назначать документированную timezone исходным datetime без offset.

### Обязательные проверки

- [x] Все trade-linked money rows сопоставляются существующей trade или дают
  объяснимую ошибку; двойного учёта денег и fee нет.
- [x] Положительное и отрицательное `qty` дают buy и sell без потери точности.
- [x] Незавершённая сделка не попадает в ledger.
- [x] Несвязанный купон создаёт income, а клиентский перевод — cash movement.
- [x] Проходит один сквозной upload -> preview -> confirm -> recalculate -> rollback.

Раздел завершён 2026-08-10: отдельный adapter `alfa_broker_xml_import` версии
`1.0` повторно применяет общую bounded XML security policy перед построением дерева,
проверяет root, metadata, обязательные collections и период. Завершённая сделка
создаёт точный trade и ненулевой `bank_tax` как отдельный fee; оба сохраняют
исходный `trade_no`, а operation type разделяет их fingerprint. Знак `qty`
однозначно задаёт buy/sell, `summ_trade` остаётся только raw traceability.

Instrument reference использует валидный `isin_reg` первым, затем provider code
из соответствующей позиции (`p_code`, с fallback на `act_id`). Synthetic ISIN
намеренно невалиден и в сквозном тесте подтверждает provider-code matching без
неявного создания инструмента. Datetime без offset получает `Europe/Moscow` и
точность фактически присутствующего времени.

Каждый `money_moves.trd_no` проверяется против завершённых сделок: найденные строки
сохраняются excluded только для reconciliation, отсутствующая связь становится
блокирующей ошибкой. Несвязанные клиентские пополнения/выводы, купоны и банковские
тарифы нормализуются отдельно; неизвестная группа остаётся на review. Позиции,
денежные totals и незавершённые сделки не создают ledger operations. Transfer
остаётся warning-строкой и требует явного исключения до confirm, пока не определена
политика переноса себестоимости.

Миграция `0009_xml_operation_source` добавила `xml_import` в источник ledger и
прошла `upgrade -> downgrade -> upgrade`; `alembic check` не обнаружил расхождений.
Ruff и mypy прошли. Пять adapter unit-тестов и существующие девять XML security
проверок прошли. Шесть PostgreSQL import-регрессий прошли, включая полный Alfa
`upload -> preview -> explicit transfer review -> confirm -> recalculate ->
rollback`, повторные confirm/rollback, четыре операции без повторного settlement/
fee, точный остаток и метрики income/fee.

## 5. Полнота, overlap отчётов и переносы бумаг

- [x] Классифицировать импорт как `full_ledger`, `period_ledger`,
  `snapshot_with_movements` или `unknown`.
- [x] Сравнивать рассчитанные позиции и деньги с контрольными итогами отчёта.
- [x] Несовпадение сохранять diagnostic, а не исправлять скрытой adjustment.
- [x] Проверить частично пересекающиеся периоды: одинаковые сделки не дублируются,
  новые сделки добавляются.
- [x] Зафиксировать поведение первого импорта не с даты открытия счёта:
  неизвестная начальная себестоимость остаётся неизвестной.
- [x] Спроектировать `instrument_transfer` либо документированную review policy для
  зачисления/списания бумаги с неизвестной себестоимостью.
- [x] Добавить ADR и миграцию только если текущего ledger действительно не хватает.

### Обязательные проверки

- [x] Два отчёта с overlap не создают повторных trade/fee/income.
- [x] Reconciliation failure не оставляет частичный confirm.
- [x] Положительный transfer без себестоимости не создаёт ложный realised P&L.

Раздел завершён 2026-08-21: полнота приведена к четырём явным значениям. Оба
provider-specific отчёта являются `period_ledger`, Universal CSV — `unknown`.
Миграция `0010_import_reconciliation` добавила batch status/summary и control data
staging rows и прошла `upgrade -> downgrade -> upgrade`; `alembic check` не
обнаружил расхождений.

Reconciliation сравнивает точные дельты `closing - opening` по валюте и
инструменту с эффектами строк того же отчёта. Overlap-дубли участвуют в проверке
report controls, но не повторяются в Ledger. Mismatch видим и не создаёт скрытый
adjustment; структурно повреждённая metadata блокирует confirm до первой записи.
Ненулевая opening position сохраняет неизвестную историческую basis, а transfer
Альфы остаётся на явном review и не превращается в trade или adjustment.

Unit-проверки покрывают matched/mismatch/invalid metadata без раскрытия ключей и
значений. PostgreSQL happy paths подтвердили Tbank overlap со старыми и новыми
trade/fee, Alfa overlap без повторного income, атомарность invalid reconciliation
и отсутствие позиции/realised P&L по исключённому transfer.

## 6. Swagger и эксплуатационный сценарий брокерского импорта

- [x] Обновить `/api/v1/import-formats` двумя provider-specific форматами.
- [x] Обновить OpenAPI upload examples и описания preview diagnostics.
- [x] В Swagger пользователь явно выбирает provider и account; adapter независимо
  подтверждает внутренний формат файла.
- [x] Добавить безопасные summary: период, counts, типы операций, warnings и
  reconciliation status без раскрытия raw строк.
- [x] Обновить runbook и limitations.

### Обязательные проверки

- [x] Оба synthetic файла можно загрузить через Swagger/API.
- [x] Неверный provider для корректного файла завершается detection error.
- [x] Confirm и rollback остаются идемпотентными для обоих адаптеров.

Раздел завершён 2026-08-21: `/api/v1/import-formats` публикует оба adapter ID и
версии, multipart OpenAPI содержит явные provider/format examples, а preview —
provider, detected version, период, полноту, reconciliation status/summary,
operation counts, currency totals и diagnostic counts. Агрегированная
reconciliation diagnostic не содержит raw строк, instrument identifiers или
контрольных сумм.

Оба synthetic fixtures проходят API upload и полный idempotent
confirm/rollback. XLSX Т‑Инвестиций, объявленный как Alfa provider, безопасно
завершает worker processing кодом `import_adapter_not_found`; выбор пользователя
не заменяет внутреннюю detection. Эксплуатационный порядок, overlap review и
ограничения зафиксированы в `RUNBOOK.md`, `LIMITATIONS.md` и ADR-025.

## 7. CEX после брокерских адаптеров

- [x] Выбрать Bybit Spot как первую CEX и изучить официальный ручной CSV export.
- [x] Добавить полностью искусственный bundle из Spot Trade History, UTA Asset
  Change Details, Funding Asset Change Details и withdraw/deposit history.
- [x] Добавить multi-document import batch: один логический batch хранит отдельный
  SHA-256 и raw traceability каждого CSV без требования собирать ZIP вручную.
- [x] Реализовать provider adapter `bybit_spot_csv_bundle` версии `1.0` с detection
  metadata/header каждого документа и проверкой общего UID без его публикации.
- [x] Спроектировать instrument-to-instrument trade для crypto-to-crypto пар.
- [x] Спроектировать fee в base, quote или третьем asset.
- [x] Сохранить обратную совместимость существующих trade и fee payload.
- [x] Зафиксировать provider-specific Bybit bundle после изучения реальной
  выгрузки; Universal CEX CSV оставить fallback, а не первым adapter.
- [x] Использовать Spot history как источник execution ID, а парные UTA `TRADE`
  rows — как источник двух asset-ног и фактического fee asset.
- [x] Дедуплицировать UTA ↔ Funding internal transfers и Funding withdrawal ↔
  on-chain history; одно движение не должно создавать две операции.
- [x] Оставлять наблюдаемые `Airdrop`, `Fiat/P2P` и `IPO` на review до отдельной
  политики, не классифицируя доход только по provider description.
- [x] Не создавать неизвестные assets без review.
- [x] Назначать UTC как явную timezone и сохранять исходную Decimal precision.

### Обязательные проверки

- [x] Bundle с отсутствующим обязательным документом или разными UID блокирует
  confirm безопасной diagnostic без идентификаторов и raw values.
- [x] Каждая Spot execution связывается ровно с двумя UTA legs; missing/ambiguous
  match требует review и не создаёт частичный trade.
- [x] Обе asset-ноги и fee сохраняют точные количества.
- [x] Fee в третьем asset не меняет торговый notional.
- [x] BUY и SELL используют fee asset из UTA, а не предположение по направлению.
- [x] Парный UTA/Funding transfer имеет нулевой эффект на общий капитал CEX.
- [x] Deposit и withdrawal не создают искусственный realised P&L.
- [x] Python calculation core повторяет эталонные результаты после расширения.

Bybit выбран 2026-08-21. Исходные пользовательские CSV остаются только вне
project tree. Четыре `docs/fixtures/bybit_*_synthetic_v1.csv` содержат новый
искусственный сценарий с наблюдаемыми headers, двумя ногами BUY/SELL, fee в
получаемом asset, внутренними transfers и on-chain withdrawal. В реальной выборке
fee в третьем asset и deposit не наблюдались, поэтому они остаются обязательными
каноническими сценариями, но не приписываются provider format без доказательства.

Раздел завершён 2026-08-21. Multipart upload принимает один или несколько полей
`file`; каждый документ хранится в `import_batch_files`, а staging-строка содержит
`source_file_id`. Bundle fingerprint не зависит от порядка выбора файлов, но
сохраняет прежний SHA-256 для однофайлового batch. `crypto_trade` хранит UUID и
точное количество обеих asset-ног; `fee` обратно совместимо принимает либо
денежный `amount/currency`, либо `instrument_id/quantity`. Calculation contract
повышен до версии 2: basis переносится между asset-ногами без искусственного
realised P&L, asset fee пропорционально уменьшает позицию. Миграция
`0011_bybit_spot_bundle` прошла upgrade → downgrade → upgrade; unit и PostgreSQL
workflow подтверждают detection, matching, review, confirm, recalculation,
duplicate upload и rollback.

## 8. Завершение Phase 02

- [x] Запустить Ruff, mypy и только риск-ориентированные тесты этой фазы.
- [x] Проверить миграции upgrade -> downgrade -> upgrade, если они добавлялись.
- [x] Проверить отсутствие персональных отчётов, credentials и raw financial data.
- [x] Проверить чистый локальный запуск и оба брокерских happy path.
- [x] Повторить benchmark только если calculation contract изменился.
- [x] Зафиксировать следующий приоритет: выбранная CEX или allocation.

Phase 02 завершена 2026-08-21. Ruff и mypy прошли; быстрый набор дал 67 passed и
12 ожидаемо skipped без PostgreSQL, отдельный PostgreSQL-набор — 12 passed. Явные
end-to-end сценарии Т‑Инвестиций и Альфа‑Инвестиций прошли `upload -> preview ->
review -> confirm -> recalculate -> rollback`. Миграция
`0011_bybit_spot_bundle` повторно прошла `upgrade -> downgrade 0010 -> upgrade`;
текущая revision является head, а `alembic check` не обнаружил расхождений.

Локально запущенный Uvicorn вернул HTTP 200 для liveness, readiness, Swagger и
OpenAPI и штатно завершился. Privacy-аудит project tree обнаружил только
документированные synthetic fixtures, Universal CSV templates и synthetic
`swagger-test.csv`; provider credentials, ключи и персональные отчёты отсутствуют.
XLSX fixture дополнительно проверен на email, телефонные, паспортные и private-key
маркеры без вывода содержимого.

Calculation contract v2 повторно проверен на 100 000 синтетических операций,
8 счетах и 250 инструментах: 2 000 позиций, 0 diagnostics, медиана 0,354798 с за
7 запусков, или 281 851 operations/s. Эти цифры являются локальной контрольной
точкой, а не межмашинным performance SLA.

Первый CEX уже реализован в разделе 7, поэтому следующим приоритетом выбрана
backend-allocation: категории активов, целевые веса и точное сравнение текущей и
целевой структуры без инвестиционных рекомендаций или торговых действий. Решение
зафиксировано в ADR-028. План находится в `PHASE_03.md`; следующий исполняемый шаг
— зафиксировать методики актуальной оценки и неполных данных.

## Не входит в Phase 02

- обычные банковские счета, карты, вклады и история покупок;
- frontend и мобильное приложение;
- подключение реальных API и хранение provider credentials;
- автоматическая торговля, переводы или вывод средств;
- универсальный PDF parser;
- налоговые рекомендации и декларации;
- Kubernetes, Kafka и преждевременное разделение на микросервисы;
- полный test coverage и тесты тривиального wiring.
