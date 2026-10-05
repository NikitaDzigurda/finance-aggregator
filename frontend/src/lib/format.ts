import Decimal from "decimal.js";

const currencySymbols: Record<string, string> = {
  RUB: "₽",
  USD: "$",
  EUR: "€",
};

export function currencyLabel(value: string): string {
  const labels: Record<string, string> = {
    RUB: "Рубли",
    USD: "Доллары США",
    EUR: "Евро",
  };
  return labels[value] ?? value;
}

export function formatDecimal(value: string, digits = 2): string {
  const [integer = "0", fraction] = new Decimal(value)
    .toDecimalPlaces(digits)
    .toFixed(digits)
    .split(".");
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return fraction === undefined ? grouped : `${grouped},${fraction}`;
}

export function formatMoney(value: string | null, currency: string): string {
  if (value === null) return "Недоступно";
  const sign = new Decimal(value).isNegative() ? "−" : "";
  return `${sign}${formatDecimal(new Decimal(value).abs().toString())} ${currencySymbols[currency] ?? currency}`;
}

export function formatSignedMoney(
  value: string | null,
  currency: string,
): string {
  if (value === null) return "Недоступно";
  const decimal = new Decimal(value);
  const prefix = decimal.isPositive() ? "+" : decimal.isNegative() ? "−" : "";
  return `${prefix}${formatDecimal(decimal.abs().toString())} ${currencySymbols[currency] ?? currency}`;
}

export function formatPercent(value: string | null): string {
  if (value === null) return "—";
  return `${formatDecimal(new Decimal(value).times(100).toString(), 1)}%`;
}

export function formatDate(value: string | null): string {
  if (!value) return "Нет данных";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function assetClassLabel(
  systemClass: string,
  fallback?: string,
): string {
  const labels: Record<string, string> = {
    cash: "Денежные средства",
    crypto: "Криптоактивы",
    derivative: "Производные инструменты",
    equity: "Акции",
    fixed_income: "Облигации",
    fund: "Фонды",
    other: "Прочее",
  };
  return labels[systemClass] ?? userFacingName(fallback ?? systemClass);
}

export function breakdownDimensionLabel(value: string): string {
  const labels: Record<string, string> = {
    account: "Счета",
    institution: "Брокеры и площадки",
    currency: "Валюты",
    instrument_type: "Типы инструментов",
  };
  return labels[value] ?? "Другой разрез";
}

export function accountTypeLabel(value: string): string {
  return value === "broker"
    ? "брокерский счёт"
    : value === "cex"
      ? "счёт криптобиржи"
      : value === "bank"
        ? "банковский счёт"
        : "другой тип счёта";
}

export function instrumentTypeLabel(value: string | null): string {
  if (!value) return "денежные средства";
  const labels: Record<string, string> = {
    stock: "акция",
    bond: "облигация",
    etf: "биржевой фонд",
    fund: "фонд",
    option: "опцион",
    crypto_asset: "криптоактив",
    currency: "валюта",
  };
  return labels[value] ?? "другой тип инструмента";
}

export function identifierTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    ticker: "биржевой код",
    isin: "международный код ценной бумаги",
    provider_code: "код поставщика",
    crypto_asset_code: "код криптоактива",
  };
  return labels[value] ?? "идентификатор";
}

export function operationTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    trade: "Сделка с инструментом",
    crypto_trade: "Сделка с криптоактивом",
    income: "Доход",
    fee: "Комиссия",
    tax: "Налог",
    cash_movement: "Движение денежных средств",
    currency_exchange: "Обмен валюты",
    crypto_transfer: "Перевод криптоактива",
    corporate_action: "Корпоративное действие",
    bond_redemption: "Погашение облигации",
    balance_adjustment: "Корректировка остатка",
  };
  return labels[value] ?? "Операция";
}

export function userFacingName(value: string | null | undefined): string {
  if (!value) return "Без названия";
  const labels: Record<string, string> = {
    "Swagger test portfolio updated": "Проверочный портфель",
    "Swagger broker account updated": "Проверочный брокерский счёт",
    "Synthetic Broker Updated": "Учебный брокер",
    "Synthetic broker account": "Учебный брокерский счёт",
    "Synthetic Broker": "Учебный брокер",
    "Synthetic CEX account": "Учебный счёт криптобиржи",
    "Synthetic Exchange": "Учебная криптобиржа",
    "Synthetic equity": "Учебная акция",
    "Synthetic Equity Updated": "Учебная акция",
    "Synthetic crypto": "Учебный криптоактив",
    "Synthetic missing price": "Учебный актив без цены",
    "Synthetic USDT": "Учебный токен USDT",
  };
  if (labels[value]) return labels[value];
  if (value.startsWith("QA · Actual allocation ")) {
    return value.replace("QA · Actual allocation ", "Учебное распределение · ");
  }
  const qaShare = /^QA ([AT]) Акция (\d+) — синтетический пример$/.exec(value);
  if (qaShare) {
    return `Учебная акция ${qaShare[1] === "A" ? "А" : "Т"} ${qaShare[2]}`;
  }
  return value;
}

export function timelineMetricLabel(value: string): string {
  const labels: Record<string, string> = {
    deposits: "Пополнения",
    withdrawals: "Выводы",
    income: "Доходы",
    fees: "Комиссии",
    taxes: "Налоги",
    trade_turnover: "Оборот сделок",
    realised_pnl: "Реализованный результат",
  };
  return labels[value] ?? "События операций";
}

export function timeZoneLabel(value: string): string {
  try {
    const parts = new Intl.DateTimeFormat("ru-RU", {
      timeZone: value,
      timeZoneName: "long",
    }).formatToParts(new Date());
    return parts.find((part) => part.type === "timeZoneName")?.value ?? value;
  } catch {
    return value;
  }
}

export function timelineUnitTypeLabel(value: string): string {
  return value === "currency"
    ? "валюта"
    : value === "asset"
      ? "актив"
      : "единица";
}

export function timelineUnitLabel(type: string, value: string): string {
  return type === "currency" ? currencyLabel(value) : value;
}

export function sourceProviderLabel(value: string): string {
  const labels: Record<string, string> = {
    tbank_broker_xlsx: "Т‑Инвестиции",
    alfa_broker_xml_import: "Альфа‑Инвестиции",
    bybit_spot_csv_bundle: "Байбит",
    universal_broker: "Универсальный брокерский отчёт",
  };
  return labels[value] ?? "Другой источник";
}

export function humanStatus(value: string): string {
  const labels: Record<string, string> = {
    complete: "Полные данные",
    partial: "Частично",
    unavailable: "Недоступно",
    uploaded: "Загружен",
    detecting: "Определение формата",
    parsing: "Обработка",
    awaiting_review: "Требует проверки",
    ready_to_commit: "Готов к подтверждению",
    committing: "Подтверждение",
    committed: "Подтверждён",
    recalculating: "Пересчёт",
    completed: "Завершён",
    failed: "Ошибка",
    rolled_back: "Отменён",
    ready: "Готово",
    warning: "Предупреждение",
    error: "Ошибка",
    duplicate: "Дубликат",
    excluded: "Исключено",
    committed_row: "Добавлено",
    pending: "Ожидает",
  };
  return labels[value] ?? "Неизвестное состояние";
}

const diagnosticCountNouns: Record<
  string,
  readonly [one: string, few: string, many: string]
> = {
  import_history_period_limited: ["импорт", "импорта", "импортов"],
  import_history_completeness_unknown: ["импорт", "импорта", "импортов"],
  import_reconciliation_mismatch: ["импорт", "импорта", "импортов"],
  import_review_unresolved: [
    "строка импорта",
    "строки импорта",
    "строк импорта",
  ],
  calculation_snapshot_missing: ["расчёт", "расчёта", "расчётов"],
  calculation_snapshot_stale: ["расчёт", "расчёта", "расчётов"],
};

function russianPlural(
  count: number,
  [one, few, many]: readonly [string, string, string],
): string {
  const absolute = Math.abs(count);
  const lastTwo = absolute % 100;
  const last = absolute % 10;
  const noun =
    lastTwo >= 11 && lastTwo <= 14
      ? many
      : last === 1
        ? one
        : last >= 2 && last <= 4
          ? few
          : many;
  return `${count} ${noun}`;
}

export function diagnosticCountLabel(code: string, count: number): string {
  return russianPlural(
    count,
    diagnosticCountNouns[code] ?? ["позиция", "позиции", "позиций"],
  );
}

const impactLabels: Record<
  string,
  {
    label: string;
    complete: string;
    partial: string;
    unavailable: string;
  }
> = {
  current_value: {
    label: "Текущая стоимость",
    complete: "рассчитана полностью",
    partial: "рассчитана частично",
    unavailable: "недоступна",
  },
  cost_basis: {
    label: "Себестоимость",
    complete: "рассчитана полностью",
    partial: "рассчитана частично",
    unavailable: "недоступна",
  },
  realised_pnl: {
    label: "Реализованный результат",
    complete: "рассчитан полностью",
    partial: "рассчитан частично",
    unavailable: "недоступен",
  },
  unrealised_pnl: {
    label: "Нереализованный результат",
    complete: "рассчитан полностью",
    partial: "рассчитан частично",
    unavailable: "недоступен",
  },
  income: {
    label: "Доходы",
    complete: "учтены полностью",
    partial: "учтены частично",
    unavailable: "недоступны",
  },
  fees: {
    label: "Комиссии",
    complete: "учтены полностью",
    partial: "учтены частично",
    unavailable: "недоступны",
  },
  taxes: {
    label: "Налоги",
    complete: "учтены полностью",
    partial: "учтены частично",
    unavailable: "недоступны",
  },
  market_value: {
    label: "Рыночная стоимость позиций",
    complete: "рассчитана полностью",
    partial: "рассчитана частично",
    unavailable: "недоступна",
  },
  actual_weight: {
    label: "Фактические доли",
    complete: "рассчитаны полностью",
    partial: "рассчитаны частично",
    unavailable: "недоступны",
  },
  ledger_history: {
    label: "История операций",
    complete: "полная",
    partial: "ограничена загруженными данными",
    unavailable: "недоступна",
  },
  ledger_events: {
    label: "События операций",
    complete: "показаны полностью",
    partial: "показаны по загруженным периодам",
    unavailable: "недоступны",
  },
  review: {
    label: "Проверка импорта",
    complete: "завершена",
    partial: "требует решения",
    unavailable: "недоступна",
  },
};

export function impactLabel(
  metric: string,
  state: string,
  endpoint?: string,
): string {
  const impact = impactLabels[metric];
  if (!impact) return `Связанный показатель: ${humanStatus(state).toLowerCase()}`;
  const contextualLabel =
    endpoint === "analytics/holdings" && metric === "cost_basis"
      ? "Себестоимость затронутых позиций"
      : endpoint === "analytics/holdings" && metric === "market_value"
        ? "Рыночная стоимость затронутых позиций"
        : endpoint === "analytics/overview" && metric === "cost_basis"
          ? "Себестоимость портфеля"
          : impact.label;
  const stateLabel =
    state === "complete"
      ? impact.complete
      : state === "partial"
        ? impact.partial
        : impact.unavailable;
  return `${contextualLabel}: ${stateLabel}`;
}

export const historicalConversionMessage =
  "События показаны в исходных валютах. Для пересчёта на дату операции не хватает исторических курсов.";

export function diagnosticMessage(code: string, fallback: string): string {
  const labels: Record<string, string> = {
    import_history_period_limited:
      "Импорт охватывает только ограниченный отчётный период",
    import_history_completeness_unknown:
      "Источник не подтверждает полноту истории операций",
    import_reconciliation_mismatch:
      "Данные импорта не совпадают с контрольными итогами отчёта",
    import_review_unresolved: "В импорте остались строки, требующие решения",
    calculation_snapshot_missing: "Расчёт портфеля ещё не выполнен",
    calculation_snapshot_stale:
      "Расчёт не включает последние сохранённые операции",
    market_price_missing: "Для одной или нескольких позиций отсутствует цена",
    market_price_stale: "Используется устаревшая цена",
    fx_rate_missing:
      "Для пересчёта одной или нескольких позиций не хватает курса",
    fx_rate_stale: "Используется устаревший валютный курс",
    cost_basis_unknown:
      "Для одной или нескольких позиций неизвестна себестоимость",
    negative_position: "Расчёт содержит отрицательную позицию",
    cost_currency_mismatch: "В позиции несовместимы валюты себестоимости",
    valuation_currency_mismatch:
      "Валюта оценки отличается от валюты себестоимости",
    allocation_component_unavailable:
      "Одна или несколько позиций исключены из фактического распределения",
    allocation_negative_component:
      "Есть отрицательный денежный остаток: точные суммы показаны в таблице, круговая диаграмма неприменима",
    allocation_denominator_nonpositive:
      "Известная стоимость недостаточна для расчёта фактических долей",
    corporate_action_basis_assumption:
      "Себестоимость после корпоративного действия сохранена по явному допущению",
    historical_reporting_conversion_unavailable: historicalConversionMessage,
  };
  return labels[code] ?? (fallback && !/[A-Za-z_]/.test(fallback) ? fallback : "Показатель рассчитан не полностью");
}

export function diagnosticExplanation(code: string): string {
  const explanations: Record<string, string> = {
    import_history_period_limited:
      "Расчёт и история событий основаны только на загруженных отчётных периодах. Добавьте более ранний отчёт, если он доступен.",
    import_history_completeness_unknown:
      "Источник не подтверждает полноту истории операций. Показатели рассчитаны только по подтверждённым данным.",
    import_reconciliation_mismatch:
      "Операции отчёта не полностью совпали с его контрольными итогами. Проверьте связанный импорт перед выводами по портфелю.",
    import_review_unresolved:
      "В импорте остались строки, для которых требуется решение. Откройте историю импортов и завершите проверку.",
    calculation_snapshot_missing:
      "Портфель ещё не пересчитан по сохранённым операциям, ценам и курсам. Запустите пересчёт.",
    calculation_snapshot_stale:
      "После последнего расчёта появились новые операции или данные. Запустите пересчёт, чтобы обновить показатели.",
    market_price_missing:
      "Поддерживаемые криптоцены обновляются фоном. Для остальных активов можно сохранить цену вручную в форме ниже. Цена помогает оценить текущую стоимость, но не восстанавливает себестоимость.",
    market_price_stale:
      "Расчёт использует ранее сохранённую цену. Для поддерживаемой криптовалюты дождитесь фонового обновления; для остальных активов можно внести новую цену вручную.",
    fx_rate_missing:
      "Для пересчёта в выбранную валюту не хватает валютного курса. Курс доллара США к российскому рублю может обновляться автоматически из Банка России; остальные курсы вводятся вручную.",
    fx_rate_stale:
      "Расчёт использует сохранённый валютный курс, который помечен устаревшим. Обновите курс доллара США к российскому рублю или введите нужный курс вручную.",
    cost_basis_unknown:
      "Нужна история приобретения или более ранний отчёт: текущая цена не восстанавливает себестоимость. Финансовый результат остальных позиций рассчитывается отдельно.",
    negative_position:
      "Продажи или списания превысили учтённое количество актива. Проверьте историю операций и более ранние отчёты по этой позиции.",
    cost_currency_mismatch:
      "Себестоимость позиции хранится в несовместимых валютах. Проверьте операции, которые сформировали позицию.",
    valuation_currency_mismatch:
      "Валюта сохранённой цены отличается от валюты себестоимости. Проверьте цену и операции позиции.",
    allocation_component_unavailable:
      "Часть позиций не вошла в распределение, потому что их текущая стоимость недоступна.",
    allocation_negative_component:
      "Отрицательный денежный остаток нельзя корректно показать сектором круговой диаграммы. Точные знаковые суммы остаются в таблице.",
    allocation_denominator_nonpositive:
      "Известной стоимости недостаточно, чтобы рассчитать фактические доли портфеля.",
    corporate_action_basis_assumption:
      "Себестоимость после корпоративного действия сохранена по явно зафиксированному правилу расчёта.",
    historical_reporting_conversion_unavailable: historicalConversionMessage,
  };
  return (
    explanations[code] ??
    "Показатель ограничен сохранёнными данными."
  );
}
