import { useQuery } from "@tanstack/react-query";
import { Search, SlidersHorizontal } from "lucide-react";
import { useMemo, useState } from "react";
import { Virtuoso } from "react-virtuoso";
import { Link, useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useWorkspace } from "../app/workspace-context";
import {
  PageHeader,
  QualityBadge,
  QueryState,
  StatePanel,
} from "../components/ui";
import {
  assetClassLabel,
  currencyLabel,
  formatDate,
  formatDecimal,
  formatMoney,
  formatSignedMoney,
  instrumentTypeLabel,
  userFacingName,
} from "../lib/format";

function CashMetricNotApplicable() {
  return (
    <span
      className="not-applicable"
      title="Не применяется к денежным средствам"
    >
      <span aria-hidden="true">—</span>
      <span className="sr-only">Не применяется к денежным средствам</span>
    </span>
  );
}

function priceKindLabel(kind: string) {
  if (kind === "manual") return "ручная цена";
  if (kind === "last_trade") return "последняя сделка";
  if (kind === "usd_index") return "индексная оценка USD";
  if (kind === "close") return "цена закрытия";
  if (kind === "global_aggregate") return "глобальная агрегированная оценка";
  return kind;
}

function MissingPriceDetail({
  instrumentId,
  portfolioId,
}: {
  instrumentId: string;
  portfolioId: string;
}) {
  const [open, setOpen] = useState(false);
  const mapping = useQuery({
    queryKey: ["market-mappings", instrumentId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/instruments/{instrument_id}/market-mappings", {
          params: { path: { instrument_id: instrumentId } },
        }),
      ),
    enabled: open,
  });
  const sync = useQuery({
    queryKey: ["market-sync-latest"],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/market-data/sync/latest")),
    enabled: open,
  });
  const coinPaprikaMapping = mapping.data?.items.some(
    (item) => item.provider === "coinpaprika" && item.eligible,
  );
  const explanation =
    mapping.data?.status === "ambiguous"
      ? "Внешний код неоднозначен. Автоматическая цена не применяется."
      : mapping.data?.status === "verified"
        ? coinPaprikaMapping &&
          sync.data?.error_code === "market_recalculation_failed"
          ? "Цена сохранена, но фоновый пересчёт пока не завершился."
          : coinPaprikaMapping && sync.data?.status === "failed"
            ? "Источник цены не ответил или прислал непригодный снимок; прежняя цена сохранена."
            : "Инструмент сопоставлен, но подходящей котировки пока нет."
        : mapping.data?.status === "unsupported"
          ? "Для этого инструмента автоматическая цена не поддерживается."
          : "Для инструмента пока нет проверенного источника цены.";
  return (
    <details
      className="price-detail"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Почему нет цены</summary>
      <span>
        {mapping.isLoading
          ? "Проверяем сопоставление…"
          : mapping.isError
            ? "Причина пока недоступна."
            : explanation}{" "}
        <Link to={`/p/${portfolioId}/data-quality`}>Внести цену вручную</Link>
      </span>
    </details>
  );
}

export function HoldingsPage() {
  const { portfolioId = "" } = useParams();
  const { currency } = useWorkspace();
  const [search, setSearch] = useState("");
  const [account, setAccount] = useState("all");
  const query = useQuery({
    queryKey: ["holdings", portfolioId, currency],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/portfolios/{portfolio_id}/analytics/holdings", {
          params: {
            path: { portfolio_id: portfolioId },
            query: { reporting_currency: currency },
          },
        }),
      ),
    refetchInterval: 30_000,
  });
  const accounts = useMemo(
    () =>
      Array.from(
        new Map(
          query.data?.items.map((item) => [
            item.account_id,
            item.account_name,
          ]) ?? [],
        ),
      ),
    [query.data],
  );
  const items = useMemo(
    () =>
      (query.data?.items ?? []).filter((item) => {
        const haystack =
          `${item.instrument_name ?? item.source_currency} ${item.account_name} ${item.category.name}`.toLowerCase();
        return (
          haystack.includes(search.toLowerCase()) &&
          (account === "all" || item.account_id === account)
        );
      }),
    [account, query.data, search],
  );
  if (query.isLoading || !query.data) return <QueryState error={query.error} />;
  return (
    <div className="page holdings-page">
      <PageHeader
        title="Активы"
        description={`Оценка на ${formatDate(query.data.valuation_as_of)} · ${query.data.snapshot_fresh ? "расчёт актуален" : "расчёт устарел"}`}
      />
      <section className="holdings-summary">
        <div>
          <span>Известная стоимость</span>
          <strong>
            {formatMoney(
              query.data.current_value.known_value,
              query.data.reporting_currency,
            )}
          </strong>
        </div>
        <div>
          <span>Покрытие</span>
          <QualityBadge state={query.data.current_value.quality} />
        </div>
        <div>
          <span>Позиции</span>
          <strong>{query.data.items.length}</strong>
        </div>
        <div>
          <span>Исключено из итога</span>
          <strong>{query.data.current_value.excluded_components}</strong>
        </div>
      </section>
      {query.data.current_value.quality === "partial" ? (
        <div className="inline-notice inline-notice--partial">
          <div className="notice-icon">!</div>
          <div>
            <strong>Стоимость показана частично</strong>
            <p>
              Инструменты без цены или валютного курса не превращены в ноль и
              исключены из полного итога.
            </p>
          </div>
        </div>
      ) : null}
      <section className="data-section holdings-table-section">
        <div className="toolbar">
          <label className="search-field">
            <Search size={17} />
            <input
              aria-label="Поиск активов"
              placeholder="Инструмент, счёт или категория"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <label className="select-field">
            <SlidersHorizontal size={16} />
            <select
              aria-label="Фильтр по счёту"
              value={account}
              onChange={(e) => setAccount(e.target.value)}
            >
              <option value="all">Все счета</option>
              {accounts.map(([id, name]) => (
                <option key={id} value={id}>
                  {userFacingName(name)}
                </option>
              ))}
            </select>
          </label>
        </div>
        {!items.length ? (
          <StatePanel title="Позиции не найдены">
            <p>
              {query.data.items.length
                ? "Измените поиск или фильтр счёта."
                : "После импорта и пересчёта позиции появятся здесь."}
            </p>
          </StatePanel>
        ) : (
          <div className="virtuoso-table holdings-table">
            <div className="vrow vrow--head">
              <span>Инструмент</span>
              <span>Счёт / организация</span>
              <span>Категория</span>
              <span>Количество</span>
              <span>Исходная валюта</span>
              <span>Себестоимость</span>
              <span>Рыночная стоимость</span>
              <span>Реализованный результат</span>
              <span>Нереализованный результат</span>
              <span>Качество</span>
            </div>
            <Virtuoso
              style={{ height: Math.min(610, 58 * items.length) }}
              data={items}
              itemContent={(_, item) => (
                <div className="vrow">
                  <span>
                    <strong>
                      {userFacingName(
                        item.instrument_name ??
                          `Денежные средства · ${item.source_currency}`,
                      )}
                    </strong>
                    <small>
                      {assetClassLabel(
                        item.category.system_class,
                        item.category.name,
                      )}{" "}
                      · {instrumentTypeLabel(item.instrument_type)}
                    </small>
                    {item.kind === "instrument" && item.price_observation ? (
                      <details className="price-detail">
                        <summary>Источник цены</summary>
                        <span>
                          {item.price_observation.source === "manual"
                            ? "Ручной ввод"
                            : item.price_observation.provider}
                          {" · "}
                          {priceKindLabel(item.price_observation.price_kind)}
                          {" · "}
                          {formatDate(item.price_observation.observed_at)}
                          {" · получено "}
                          {formatDate(item.price_observation.fetched_at)}
                          {" · "}
                          {item.price_observation.currency}
                        </span>
                      </details>
                    ) : item.kind === "instrument" &&
                      item.market_value === null &&
                      item.instrument_id ? (
                      <MissingPriceDetail
                        instrumentId={item.instrument_id}
                        portfolioId={portfolioId}
                      />
                    ) : null}
                  </span>
                  <span>
                    {userFacingName(item.account_name)}
                    <small>
                      {userFacingName(
                        item.institution_name ??
                          (item.account_type === "cex"
                            ? "Криптобиржа"
                            : "Брокер"),
                      )}
                    </small>
                  </span>
                  <span>
                    {assetClassLabel(
                      item.category.system_class,
                      item.category.name,
                    )}
                  </span>
                  <span className="numeric">
                    {item.quantity === null
                      ? "—"
                      : formatDecimal(item.quantity, 4)}
                  </span>
                  <span>{currencyLabel(item.source_currency)}</span>
                  <span className="numeric">
                    {item.kind === "cash" ? (
                      <CashMetricNotApplicable />
                    ) : (
                      formatMoney(item.cost_basis, item.reporting_currency)
                    )}
                  </span>
                  <span className="numeric">
                    {formatMoney(item.market_value, item.reporting_currency)}
                  </span>
                  <span className="numeric numeric--signed">
                    {formatSignedMoney(
                      item.realised_pnl,
                      item.reporting_currency,
                    )}
                  </span>
                  <span className="numeric numeric--signed">
                    {item.kind === "cash" ? (
                      <CashMetricNotApplicable />
                    ) : (
                      formatSignedMoney(
                        item.unrealised_pnl,
                        item.reporting_currency,
                      )
                    )}
                  </span>
                  <span>
                    <QualityBadge state={item.quality} />
                  </span>
                </div>
              )}
            />
          </div>
        )}
      </section>
    </div>
  );
}
