import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  CircleHelp,
  TriangleAlert,
} from "lucide-react";
import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";
import { Link, useParams } from "react-router-dom";
import { api, unwrap, type Schema } from "../api/client";
import { useWorkspace } from "../app/workspace-context";
import {
  ButtonLink,
  QualityBadge,
  QueryState,
  StatePanel,
} from "../components/ui";
import {
  assetClassLabel,
  diagnosticCountLabel,
  diagnosticMessage,
  formatDate,
  formatMoney,
  formatPercent,
  formatSignedMoney,
  userFacingName,
} from "../lib/format";

type Metric = Schema<"CurrentMetricResponse">;

function MetricBlock({
  label,
  help,
  metric,
  signed = false,
}: {
  label: string;
  help: string;
  metric: Metric;
  signed?: boolean;
}) {
  const formatter = signed ? formatSignedMoney : formatMoney;
  const visibleValue =
    metric.quality === "partial" ? metric.known_value : metric.total_value;
  return (
    <article className={`metric metric--${metric.quality}`}>
      <div className="metric__label">
        <span>{label}</span>
        <span
          className="metric__help"
          title={help}
          aria-label={help}
          tabIndex={0}
        >
          <CircleHelp size={15} aria-hidden="true" />
        </span>
      </div>
      <strong className={signed ? "numeric numeric--signed" : "numeric"}>
        {formatter(visibleValue, metric.reporting_currency)}
      </strong>
      {metric.quality !== "complete" ? <QualityBadge state={metric.quality} /> : null}
      {metric.quality === "partial" ? (
        <small>Показана известная часть</small>
      ) : metric.quality === "unavailable" ? (
        <small>Недостаточно данных для расчёта</small>
      ) : null}
    </article>
  );
}

export function OverviewPage() {
  const { portfolioId = "" } = useParams();
  const { currency } = useWorkspace();
  const overview = useQuery({
    queryKey: ["overview", portfolioId, currency],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/portfolios/{portfolio_id}/analytics/overview", {
          params: {
            path: { portfolio_id: portfolioId },
            query: { reporting_currency: currency },
          },
        }),
      ),
    refetchInterval: 30_000,
  });
  const holdings = useQuery({
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
  const quality = useQuery({
    queryKey: ["quality", portfolioId, currency],
    queryFn: async () =>
      unwrap(
        await api.GET(
          "/api/v1/portfolios/{portfolio_id}/analytics/data-quality",
          {
            params: {
              path: { portfolio_id: portfolioId },
              query: { reporting_currency: currency },
            },
          },
        ),
      ),
    refetchInterval: 30_000,
  });
  const allocation = useQuery({
    queryKey: ["allocation", portfolioId, currency],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/portfolios/{portfolio_id}/allocation", {
          params: {
            path: { portfolio_id: portfolioId },
            query: { reporting_currency: currency },
          },
        }),
      ),
    refetchInterval: 30_000,
  });
  if (overview.isLoading || !overview.data)
    return <QueryState error={overview.error} />;
  const data = overview.data;
  const hasLedger = data.ledger_operation_count > 0;
  const allocationPalette = [
    "#007a77",
    "#28a7a5",
    "#63b7e6",
    "#7189ad",
    "#c2c8d1",
  ];
  const chartData = (allocation.data?.items ?? [])
    .filter((item) => item.actual_weight !== null)
    .map((item) => ({
      name: assetClassLabel(item.category.system_class, item.category.name),
      value: Number(item.actual_weight),
      amount: item.known_value,
      weight: item.actual_weight,
    }));
  const hasNegativeAllocation = allocation.data?.diagnostics.some(
    (item) => item.code === "allocation_negative_component",
  );
  const canRenderAllocationChart =
    chartData.length > 0 && !hasNegativeAllocation;
  return (
    <div className="page overview-page">
      <h1 className="sr-only">Обзор портфеля</h1>
      {!hasLedger ? (
        <StatePanel
          title="Портфель пока пуст"
          action={
            <ButtonLink to={`/p/${portfolioId}/imports/new`}>
              Импортировать отчёт
            </ButtonLink>
          }
        >
          <p>
            Загрузите брокерский отчёт или выписку криптобиржи. Данные попадут в
            расчёты только после проверки и подтверждения.
          </p>
        </StatePanel>
      ) : null}
      <div className="overview-meta">
        <span>
          Оценка на {formatDate(data.snapshot_as_of)}
          {!data.snapshot_fresh ? " · ожидает пересчёта" : ""}
        </span>
      </div>
      <section className="overview-metrics">
        <MetricBlock
          label="Текущая стоимость"
          help="Стоимость денежных остатков и позиций по доступным рыночным ценам. При неполных данных показана только рассчитанная часть."
          metric={data.current_value}
        />
        <MetricBlock
          label="Вложено в текущие позиции"
          help="Стоимость приобретения активов, которые ещё находятся в портфеле. Пополнения и свободные деньги сюда не входят."
          metric={data.cost_basis}
        />
        <MetricBlock
          label="Реализованный результат"
          help="Зафиксированный финансовый результат по уже закрытым сделкам."
          metric={data.realised_pnl}
          signed
        />
        <MetricBlock
          label="Нереализованный результат"
          help="Разница между текущей оценкой открытых позиций и стоимостью их приобретения."
          metric={data.unrealised_pnl}
          signed
        />
      </section>
      {quality.data?.diagnostics.some(
        (item) => item.code === "market_price_missing",
      ) ? (
        <Link className="inline-notice inline-notice--partial" to={`/p/${portfolioId}/data-quality`}>
          Часть цен недоступна · Посмотреть подробности
        </Link>
      ) : null}
      <section className="overview-middle">
        <article className="allocation-card">
          <div className="module-title">
            <div>
              <h2>Распределение по категориям</h2>
              <p>
                <QualityBadge state={allocation.data?.quality ?? "partial"} />{" "}
                Известная часть портфеля
              </p>
            </div>
            <CircleHelp size={16} />
          </div>
          {chartData.length ? (
            <div className="allocation-card__content">
              {canRenderAllocationChart ? (
                <div className="overview-donut" data-testid="allocation-donut">
                  <ResponsiveContainer width="100%" height={236}>
                    <PieChart>
                      <Pie
                        data={chartData}
                        dataKey="value"
                        innerRadius={64}
                        outerRadius={104}
                        paddingAngle={1}
                        stroke="#fff"
                        strokeWidth={2}
                      >
                        {chartData.map((_, index) => (
                          <Cell
                            key={index}
                            fill={
                              allocationPalette[
                                index % allocationPalette.length
                              ]
                            }
                          />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                  <div>
                    <strong>
                      {formatMoney(
                        allocation.data?.denominator ?? null,
                        currency,
                      )}
                    </strong>
                    <span>текущая стоимость</span>
                  </div>
                </div>
              ) : (
                <div className="allocation-chart-note" role="note">
                  <TriangleAlert size={24} aria-hidden="true" />
                  <strong>Круговая диаграмма неприменима</strong>
                  <span>
                    В портфеле есть отрицательный денежный остаток. Точные
                    знаковые суммы и доли показаны рядом.
                  </span>
                </div>
              )}
              <div className="allocation-legend">
                {chartData.map((item, index) => (
                  <div key={item.name}>
                    <i
                      style={{
                        background:
                          allocationPalette[index % allocationPalette.length],
                      }}
                    />
                    <span>{item.name}</span>
                    <strong>{formatPercent(item.weight)}</strong>
                    <em>{formatMoney(item.amount, currency)}</em>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="muted">
              Распределение появится после расчёта известных позиций.
            </p>
          )}
          <Link className="module-link" to={`/p/${portfolioId}/allocation`}>
            Открыть распределение <ArrowRight size={15} />
          </Link>
        </article>
        <aside className="attention-card">
          <h2>Требует внимания</h2>
          <div className="attention-list">
            {(quality.data?.diagnostics ?? [])
              .slice(0, 3)
              .map((item, index) => (
                <Link
                  to={`/p/${portfolioId}/data-quality`}
                  key={`${item.code}-${index}`}
                >
                  <span
                    className={
                      item.code.includes("missing") ? "danger" : "warning"
                    }
                  >
                    {item.code.includes("missing") ? (
                      <AlertCircle />
                    ) : (
                      <TriangleAlert />
                    )}
                  </span>
                  <span>
                    <strong>
                      {diagnosticMessage(item.code, item.message)}
                    </strong>
                    <small>{diagnosticCountLabel(item.code, item.count)}</small>
                  </span>
                  <ArrowRight size={16} />
                </Link>
              ))}
            {!quality.data?.diagnostics.length ? (
              <div className="attention-ready">
                <CheckCircle2 />
                <span>
                  <strong>Данные готовы</strong>
                  <small>Критичных вопросов нет</small>
                </span>
              </div>
            ) : null}
          </div>
          <Link className="module-link" to={`/p/${portfolioId}/data-quality`}>
            Все вопросы качества данных <ArrowRight size={15} />
          </Link>
        </aside>
      </section>
      <section className="overview-bottom">
        <article className="data-section overview-holdings">
          <div className="section-heading">
            <div>
              <h2>Крупнейшие позиции</h2>
            </div>
            <ButtonLink to={`/p/${portfolioId}/holdings`} variant="ghost">
              Все активы <ArrowRight size={16} />
            </ButtonLink>
          </div>
          {holdings.isLoading ? (
            <div className="table-loading">Загрузка позиций…</div>
          ) : holdings.data?.items.length ? (
            <div className="largest-assets">
              {holdings.data.items.slice(0, 4).map((holding) => (
                <Link
                  to={`/p/${portfolioId}/holdings`}
                  key={`${holding.account_id}-${holding.instrument_id ?? holding.source_currency}`}
                >
                  <span className="asset-icon" aria-hidden="true">
                    {holding.instrument_type === "crypto_asset" ? "₿" : "↗"}
                  </span>
                  <span>
                    <strong>
                      {userFacingName(
                        holding.instrument_name ??
                          `Денежные средства · ${holding.source_currency}`,
                      )}
                    </strong>
                    <small>
                      {assetClassLabel(
                        holding.category.system_class,
                        holding.category.name,
                      )}
                    </small>
                  </span>
                </Link>
              ))}
            </div>
          ) : (
            <p className="muted">
              После расчёта здесь появятся инструменты и денежные остатки.
            </p>
          )}
        </article>
        <article className="income-card">
          <h2>Доходы и расходы</h2>
          <div>
            <span>Доходы</span>
            <strong>{formatMoney(data.income.known_value, currency)}</strong>
          </div>
          <div>
            <span>Комиссии</span>
            <strong>{formatMoney(data.fees.known_value, currency)}</strong>
          </div>
          <div>
            <span>Налоги</span>
            <strong>{formatMoney(data.taxes.known_value, currency)}</strong>
          </div>
        </article>
      </section>
    </div>
  );
}
