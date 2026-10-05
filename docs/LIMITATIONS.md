# Известные ограничения версии 0.1

## Доступ и эксплуатация

- Прототип рассчитан на локальную доверенную среду. Аутентификация, авторизация,
  TLS, rate limiting и разделение данных пользователей не реализованы.
- Compose использует локальные development credentials. Сервис нельзя публиковать
  в интернет без отдельного security и deployment этапа.
- Исходные импорты хранятся в локальном Docker volume или каталоге ObjectStorage.
  Шифрование at rest, retention policy, backup и внешнее object storage отсутствуют.
- Worker обрабатывает import parse, системный public `fx_sync` и общий
  CoinPaprika `market_data_sync` для пяти проверенных криптоактивов. Российские
  акции/фонды пока используют ручные цены; MOEX требует отдельный договор.
  Confirm, rollback
  и recalculation выполняются синхронно API. Простое FX-расписание встроено в worker.
- Наблюдаемость ограничена health checks и структурированными логами; метрики,
  tracing, alerting и production SLO не настроены.

## Источники и импорт

- Рабочие adapters: `universal_broker` CSV 1.0, официальный
  `tbank_broker_xlsx` 1.0 и `alfa_broker_xml_import` 1.0. PDF остаётся допустимым
  upload-контейнером без зарегистрированного parser и завершится безопасной
  ошибкой detection.
- Provider-specific compatibility подтверждена только полностью искусственными
  fixtures наблюдаемой структуры. Каждая новая обезличенная версия официального
  XLSX/XML требует regression validation; adapter не является полной
  спецификацией формата брокера.
- Оба брокерских отчёта классифицируются как `period_ledger`. Reconciliation
  сравнивает движения периода с report controls, но mismatch не блокирует confirm
  и не доказывает полноту всей истории счёта.
- Ненулевая позиция на начало первого отчёта не получает вымышленную покупку:
  историческая себестоимость остаётся неизвестной. Transfers бумаг Альфы требуют
  явного исключения до появления канонического контракта переноса basis.
- Приватные API брокеров и CEX не подключены, provider credentials не хранятся.
  Внешние подключения ограничены системными read-only CBR FX и CoinPaprika
  crypto gateway без пользовательских данных.
- Bybit Spot поддерживается как ручной four-document CSV bundle. Подтверждённая
  structural выборка пока не содержит deposit и fee в третьем asset; эти варианты
  поддержаны каноническим контрактом, но не имитируются provider adapter без
  доказательства формата.
- Universal broker CSV не создаёт неизвестные инструменты. Сопоставление использует
  точный ISIN или exchange+ticker+currency; fuzzy matching отсутствует.
- Ненулевой accrued interest сохраняется как diagnostic и не превращается в
  вымышленную операцию.
- Rollback запрещён, если импортную операцию уже использует correction operation.
  Это защищает трассируемость, но требует отдельного ручного исправления истории.

## Ledger и справочники

- Ledger неизменяем: существующую операцию нельзя обновить или удалить через API.
  Исправление создаётся новой компенсирующей operation с audit note.
- Валюта представлена трёхбуквенным fiat-кодом; crypto assets имеют отдельные
  instrument UUID и не считаются эквивалентными fiat без явной цены.
- CEX `crypto_trade` поддерживает две instrument-ноги, а asset fee — отдельное
  точное количество. Историческая fiat-оценка таких ног без market data остаётся
  недоступной.
- Allocation ограничен системными классами, portfolio custom categories,
  instrument overrides и фактическими весами по известному denominator. Target
  plan, целевые доли, sector/country/issuer taxonomy, рекомендации и генерация
  ребалансирующих сделок не входят в MVP.

## Расчёты и цены

- Поддерживается только weighted-average cost basis. FIFO и другие налоговые
  политики отсутствуют.
- CoinPaprika Free автоматически обновляет глобальную USD-цену пяти проверенных
  криптоактивов через worker; прочие активы, включая российские акции и фонды,
  пока используют ручной ввод. USD/RUB может синхронизироваться из
  официального дневного XML Банка России; это не intraday market quote. Другие FX
  допускают ручной ввод и bounded direct/inverse/один RUB pivot.
- Worker ставит `fx_sync` при старте и далее примерно раз в час; запуск через
  API/UI также доступен. Нужны работающий worker и доступ к публичному ЦБ.
  Stale observation остаётся доступным с diagnostic, а не подменяется новым
  значением. Старые portfolio snapshots требуют пересчёта.
- Current analytics консолидирует snapshot в RUB или USD. Исторические Ledger
  events пока остаются в исходных валютах; применять к ним текущий FX запрещено.
- Хранится один заменяемый snapshot на портфель. Исторические версии snapshot и
  расчёт временных рядов отсутствуют.
- Transfer или положительная adjustment без себестоимости создаёт diagnostic;
  неизвестная себестоимость не подменяется нулём.
- TWR, XIRR, Sharpe, Sortino, beta, прогнозы, полная риск-аналитика и налоговая
  отчётность не реализованы.
- Python-ядро остаётся эталоном. C++/pybind11 отложен до профилирования реальной
  нагрузки.

## API

- Списки используют limit/offset без total count и cursor pagination.
- Event timelines являются исключением: они возвращают `series_total`, применяют
  `limit/offset` к устойчиво отсортированным series и ограничены 366 днями. Buckets
  внутри series не пагинируются; для большего периода клиент делает несколько
  неперекрывающихся запросов.
- OpenAPI описывает текущую версию `/api/v1`; политика внешней обратной
  совместимости и lifecycle устаревших версий ещё не введены.

## Frontend

- Web frontend предназначен только для локальной доверенной среды. Production
  hosting, auth, multi-user isolation, mobile application и offline mode не
  реализованы.
- Compose frontend раздаётся минимальным Node static server с внутренним API
  proxy. TLS, compression, CDN/cache invalidation policy и production reverse
  proxy не входят в этот локальный runtime; nginx намеренно не добавлен.
- UI покрывает Phase 04 Operate flow, но не предоставляет ручную форму для всех
  вариантов Ledger operation/correction. Для редких административных операций
  Swagger остаётся диагностическим fallback.
- Список инструментов в browser загружает максимум 100 элементов и фильтруется
  локально. Для крупного справочника потребуется server-side search/pagination.
- Графики allocation и Ledger events используют преобразование exact backend
  strings в browser `number` только как координаты presentation. Те же значения
  доступны таблицей; frontend не использует геометрию для финансовых расчётов.
- Историческая стоимость портфеля и performance-графики отсутствуют, потому что
  backend не предоставляет daily valuation series. Ledger timelines не выдаются
  за доходность.
- Поддерживаются актуальные desktop Chromium/Firefox/WebKit и tablet layout от
  760 до 1020 px; отдельная mobile-композиция ниже 760 px остаётся упрощённой web
  адаптацией, а не заявленным mobile product.
- Автоматические instrument prices отсутствуют из-за неподтверждённых прав на
  использование кандидатов (ADR-053): data-readiness предлагает ручной batch
  input. Подготовленная схема может хранить происхождение наблюдения, но это не
  означает действующий live-адаптер. CBR sync покрывает только системный
  официальный FX сценарий, остальные курсы вводятся вручную.
