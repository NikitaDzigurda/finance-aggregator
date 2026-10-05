# Проектирование импорта

## Цель

Пользователь не должен вручную переделывать официальный отчёт под внутреннюю схему сервиса. Он выбирает источник и счёт, загружает файл как есть, проверяет preview и подтверждает результат.

Универсальные шаблоны используются как fallback для неподдерживаемых источников.

## Пользовательский сценарий

```text
1. Выбрать источник
2. Выбрать счёт
3. Загрузить CSV/XLSX/XML/PDF
4. Дождаться разбора
5. Просмотреть автоматически подготовленный итог
6. Разрешить только настоящие неоднозначности и дубли
7. Подтвердить
8. Получить результат пересчёта
```

## Конвейер

```text
upload
  -> hash and store original
  -> detect adapter and document version
  -> parse raw rows/sections
  -> normalize typed operations
  -> validate
  -> match instruments
  -> detect duplicates
  -> preview
  -> confirm atomically
  -> recalculate
```

## Сущности

### ImportBatch

- ID;
- portfolio/account;
- source provider;
- declared format;
- detected format/version;
- original filename;
- storage key;
- SHA-256;
- reporting period;
- completeness classification;
- status;
- counters;
- reconciliation status и безопасный summary;
- timestamps;
- error summary.

### ImportRow

- batch ID;
- source page/sheet/row;
- raw representation;
- normalized operation candidate;
- машинные reconciliation controls/effects;
- status;
- warnings/errors;
- fingerprint;
- matched operation ID after commit.

### ImportResolution

Решение пользователя или адаптера для неоднозначности:

- сопоставление инструмента;
- выбор валюты;
- исключение строки;
- подтверждение возможного дубля;
- исправление типа операции.

## Статусы

```text
uploaded
detecting
parsing
awaiting_review
ready_to_commit
committing
committed
recalculating
completed
failed
rolled_back
```

## API первой версии

```text
POST   /api/v1/imports
GET    /api/v1/imports/{id}
GET    /api/v1/imports/{id}/preview
GET    /api/v1/imports/{id}/rows
PATCH  /api/v1/imports/{id}/rows/{row_id}
POST   /api/v1/imports/{id}/confirm
POST   /api/v1/imports/{id}/rollback
GET    /api/v1/import-formats
```

`confirm` должен быть идемпотентным. Повторный запрос возвращает существующий результат.

## Адаптеры

```python
class ImportAdapter(Protocol):
    format_id: str
    version: str

    def detect(self, document: ImportDocument) -> DetectionResult: ...
    def parse(self, document: ImportDocument) -> ParsedImport: ...
    def validate(self, parsed: ParsedImport) -> ValidationResult: ...
```

Адаптер не пишет напрямую в ledger. Он возвращает кандидатов операций и диагностику.
Для multi-document формата `ImportDocument.bundle_documents` содержит все документы
одного логического batch; однофайловые adapters продолжают читать корневой document.
Каждая `ParsedRow` указывает `source_document_index`, который worker преобразует в
публичный `source_file_id` без раскрытия storage key.

## Полнота и reconciliation

Batch классифицируется одним из значений:

- `full_ledger` — источник гарантирует полную историю счёта;
- `period_ledger` — операции ограничены отчётным периодом;
- `snapshot_with_movements` — источник даёт снимок и движения, но не полный ledger;
- `unknown` — достаточной гарантии источника нет.

Т‑Инвестиции XLSX v1 и Альфа XML import v1 являются `period_ledger`. Universal
broker CSV остаётся `unknown`, так как сам файл не подтверждает дату открытия
счёта или полноту истории.

Контрольные остатки не превращаются в операции. Для period report сверяются точные
дельты `closing - opening` по валюте и инструменту с экономическими эффектами строк
этого же отчёта. Дубли overlap batch участвуют в сверке отчёта, но не проходят в
Ledger без review. `mismatch` остаётся видимой diagnostic и никогда не исправляется
скрытым adjustment. Некорректная машинная metadata блокирует confirm до записи
операций. Ненулевая начальная позиция получает diagnostic неизвестной исторической
себестоимости; начальная покупка не выдумывается.

## Форматы

### Universal broker CSV v1

```text
Date, Type, Symbol, ISIN, Quantity, Price, Currency,
Fee, FeeCurrency, Tax, AccruedInterest, Exchange, ExternalId, Note
```

### Т‑Инвестиции broker XLSX v1

`format_id`: `tbank_broker_xlsx`, версия `1.0`, timezone исходного времени:
`Europe/Moscow`.

Adapter поддерживает две наблюдавшиеся официальные компоновки одного provider
format:

- исторический отчёт с листом `broker_rep` и секциями в одном листе;
- актуальный многостраничный отчёт с листами `Динамика позиций`, `Завершенные
  сделки`, `Незавершенные сделки`, ` Движение ДС` и `Неторговые операции`.

Detection проверяет одновременно:

- контейнер XLSX;
- одну из двух известных внутренних структур, а не только расширение или имя;
- для исторической структуры — лист `broker_rep`, заголовок отчёта и секции
  сделок, денежных средств и движения ценных бумаг;
- для актуальной структуры — обязательный набор именованных листов и их headers;
- обязательные колонки сделки, включая номер, дату, актив, код, количество,
  цену, валюту и комиссии.

Основные правила нормализации:

- исполненная сделка создаёт `trade` и отдельные `fee`, если они ненулевые;
- номер сделки используется как внешний ID в пределах provider и account;
- ISIN имеет приоритет при сопоставлении инструмента, затем provider code;
- справочные секции инструментов помогают сопоставлению и позволяют безопасно
  создать отсутствующую запись справочника по ISIN/provider code; это не создаёт
  ledger operation;
- завершённые сделки актуального отчёта используют знак количества для
  направления, а Excel-апостроф перед кодом удаляется как форматирование;
- пользовательское пополнение/вывод из листа движения денежных средств создаёт
  `cash_movement`; связанные со сделками и контрольные строки не дублируются;
- денежные остатки и движение бумаг служат для reconciliation;
- пустая секция является допустимой и не подменяется нулевой операцией.

Структурный пример находится в
`docs/fixtures/tbank_broker_report_synthetic_v1.xlsx`. Он полностью искусственный
и не является копией пользовательского отчёта.

### Альфа‑Инвестиции broker XML import v1

`format_id`: `alfa_broker_xml_import`, версия `1.0`, timezone исходного времени:
`Europe/Moscow`.

Detection проверяет:

- XML без DTD и внешних сущностей;
- корневой элемент `report_broker`;
- обязательные metadata и коллекции `positions`, `trades_finished`,
  `trades_unfinished`, `money_moves`, `transfers`;
- допустимый размер, глубину и число элементов до построения полного staging.

Правила секций:

- `trades_finished/trade` создаёт `trade`; знак `qty` определяет направление,
  а `summ_trade` не используется как знак операции;
- ненулевой `bank_tax` создаёт отдельный `fee` с тем же source trade ID и
  отдельным operation type в fingerprint;
- `summ_nkd` сохраняется для проверки расчёта по облигации и не создаёт
  самостоятельный income;
- `trades_unfinished` попадает в preview как неподтверждаемая информационная
  строка и не влияет на ledger;
- `money_moves` с `trd_no` сверяется с соответствующей сделкой. Строки «Расчеты
  по сделке», «НКД по сделке» и «Комиссия по сделке» не импортируются второй раз;
- несвязанные `money_moves` нормализуются по `oper_group`: пополнение/вывод как
  `cash_movement`, купон как `income`, банковский тариф как `fee`;
- `positions` и `money_moves_total` используются как контрольные снимки;
- `transfers` ценной бумаги создаёт `balance_adjustment`: количество учитывается,
  но отсутствующая историческая себестоимость остаётся явным partial-quality
  состоянием и не превращается в вымышленную покупку;
- даты принимаются в фактическом provider-формате `DD.MM.YYYY H:MM:SS` и в
  совместимом ISO-варианте предыдущей structural fixture.

Datetime в XML не содержит offset. Adapter применяет `Europe/Moscow` и не помечает
исходное время более точным, чем оно есть в отчёте. Если provider начнёт отдавать
явный offset или другую timezone, это требует проверки совместимости формата.

Структурный пример находится в
`docs/fixtures/alfa_broker_report_import_synthetic_v1.xml`. Он содержит только
искусственные ФИО, договор, инструменты, суммы и идентификаторы.

### Bybit Spot CSV export bundle v1

Первый CEX source — официальный ручной export Bybit. Логический формат
`bybit_spot_csv_bundle`, версия `1.0`, timezone всех наблюдаемых файлов — UTC.
Источник состоит из четырёх связанных CSV:

- `unifiedAccount_spotTradeHistory_*` — authoritative execution и внешний
  transaction/order ID;
- `AssetChangeDetails_uta_*` — две asset-ноги Spot execution, fee asset и
  внутренние движения UTA;
- `AssetChangeDetails_fund_*` — Funding movements;
- `assetHistory_withdrawDepositHistory_*` — on-chain deposit/withdrawal details.

Каждый файл начинается metadata-строкой `UID: ...,Company Name: ...,Country: ...`,
за которой следует собственная строка headers. UID используется только для
проверки согласованности bundle и не публикуется в diagnostics.

Spot Trade History не содержит `FeeAsset`. Исполнение связывается с двумя UTA
`TRADE` rows по contract, direction, UTC timestamp и точным quantity/value.
Несколько исполнений в одну секунду разбираются по уникальной паре объёмов, а не
отправляются пользователю на ручное сопоставление. Отрицательная UTA
нога является отданным активом, положительная — полученным; отрицательный
`Fee Paid` указывает asset комиссии. Filled value/quantity, UTA quantities и fee
должны совпасть до подтверждения. Не выводить fee asset только из BUY/SELL.

Внутренний UTA `TRANSFER_OUT` соответствует Funding `Transfer in`, а обратный
перевод — паре UTA `TRANSFER_IN`/Funding `Transfer out`. Такое движение не меняет
совокупный капитал CEX account и не создаёт realised P&L. Funding withdrawal и
on-chain history также связываются и создают одну, а не две операции.
Наблюдаемая разница времени до одной секунды между двумя сторонами внутреннего
перевода считается особенностью экспорта Bybit и сопоставляется автоматически.

Наблюдавшиеся `Airdrop`, `Fiat/P2P` и `IPO` нормализуются как точные
`balance_adjustment` по asset и quantity. Они изменяют количество, но не выдумывают
цену, себестоимость или realised P&L. Fee в
третьем asset в предоставленной структуре не наблюдался; канонический контракт
должен его поддерживать, но provider fixture не должен выдумывать такую строку.

Structural fixtures находятся в `docs/fixtures/bybit_*_synthetic_v1.csv`. Они
полностью искусственные и сохраняют только наблюдаемую форму и связи.

Канонический результат Spot execution — `crypto_trade` с
`sold_instrument_id/sold_quantity` и `bought_instrument_id/bought_quantity`.
Комиссия создаётся отдельной `fee` с `instrument_id/quantity`; прежняя денежная
форма `amount/currency` остаётся допустимой. Для bundle хранятся отдельные storage
object, размер и SHA-256 каждого CSV, а deduplication всего batch использует
порядок-независимый digest набора. UID удаляется из staging raw data и никогда не
включается в diagnostics.

Universal CEX CSV остаётся возможным fallback, но не является первым реальным
адаптером:

```text
Timestamp, Type, BaseAsset, QuoteAsset, BaseQuantity,
QuoteQuantity, Fee, FeeAsset, ExternalId, Note
```

Не объединять `Fee` и `Tax`. Не перегружать `Price` и `Quantity` разными
несвязанными значениями без типизированного контекста.

## PDF

PDF классифицируется по содержимому и полноте:

```text
ledger_report
snapshot_report
analytics_report
cash_statement
unknown
```

Агрегированный аналитический отчёт нельзя импортировать как вымышленные отдельные сделки.

Наблюдения по реальным документам:

- официальный XLSX Т‑Инвестиций имеет транзакционную схему сделок, денег,
  движения ценных бумаг, комиссий и справочника инструментов;
- официальный XML «для импорта» Альфа‑Инвестиций содержит отдельные коллекции
  позиций, завершённых и незавершённых сделок, денежных движений и переводов;
- заполненный XML Альфы подтверждает прямую связь денежных расчётов, НКД и
  комиссий со сделками через `trd_no`, поэтому эти строки нельзя учитывать дважды;
- исходные персональные файлы не должны попадать в рабочую директорию проекта;
- для тестов создаются искусственные или обезличенные fixtures.

PDF — fallback. При наличии CSV/XLSX/API они предпочтительнее.

## Дедупликация

Приоритет:

1. `provider + account + source_operation_id`;
2. идентификатор сделки/поручения;
3. устойчивый fingerprint нормализованных полей;
4. возможный дубль, требующий подтверждения.

Fingerprint включает релевантные поля:

```text
account
operation type
timestamp/date precision
instrument/assets
quantity
price/amount
currencies
fees
provider description
```

## Проверки целостности

- контроль итогов из отчёта;
- баланс входящий + движения = исходящий;
- позиция входящая + покупки - продажи + corporate actions = исходящая;
- сумма компонентов сделки совпадает с итогом;
- комиссии и налоги имеют валюту;
- отрицательные количества допускаются только когда это разрешено типом операции;
- trusted provider adapter может автоматически создать справочную запись
  неизвестного инструмента только при однозначном provider identifier и
  согласованных name/type/currency; universal/fallback import и противоречивые
  metadata по-прежнему требуют явного сопоставления.

## Граница автоматической обработки и review

Официальный отчёт поддерживаемого provider загружается без преобразования. UI не
показывает пользователю reconciliation-only строки, внутренние стороны transfer,
агрегатные остатки и прочие технические исключения как «ошибки». Они остаются в
staging для трассировки и машинной сверки.

Review требуется только когда автоматическое решение может изменить финансовый
смысл: противоречивый или отсутствующий идентификатор инструмента, неоднозначное
сопоставление, возможный дубль, нарушенная связь ног сделки либо реальная ошибка
структуры. Готовые строки подтверждаются без ручного исключения каждой служебной
строки. Выбранный provider должен соответствовать типу счёта: Т‑Инвестиции и
Альфа — `broker`, Bybit — `cex`.

## Rollback

Каждая созданная операция связана с `ImportBatch`. Откат отменяет влияние этого batch и запускает пересчёт. Ручные операции и другие импорты не затрагиваются.

Если операция уже была вручную изменена после импорта, rollback должен остановиться или потребовать явного решения, а не терять изменения.
