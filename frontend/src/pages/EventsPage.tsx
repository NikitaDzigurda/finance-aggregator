import { useQuery } from "@tanstack/react-query";
import { BarChart3, CalendarDays, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useParams } from "react-router-dom";
import { api, unwrap, type Schema } from "../api/client";
import { Button, PageHeader, QueryState, StatePanel } from "../components/ui";
import {
  diagnosticMessage,
  formatDecimal,
  formatDate,
  historicalConversionMessage,
  timeZoneLabel,
  timelineMetricLabel,
  timelineUnitLabel,
  timelineUnitTypeLabel,
} from "../lib/format";

type TimelineKind = "cash-flows" | "income" | "costs" | "trading";
const labels: Record<TimelineKind, string> = {
  "cash-flows": "Пополнения и выводы",
  income: "Доходы",
  costs: "Комиссии и налоги",
  trading: "Торговые события",
};
const colors = ["#007a77", "#2678dd", "#28a7a5", "#7d56cb", "#7189ad"];

function isoDate(date: Date) {
  return date.toISOString().slice(0, 10);
}

export function EventsPage() {
  const { portfolioId = "" } = useParams();
  const [kind, setKind] = useState<TimelineKind>("cash-flows");
  const [view, setView] = useState<"chart" | "table">("chart");
  const [unitType, setUnitType] = useState<"all" | "currency" | "asset">("all");
  const [unit, setUnit] = useState("");
  const [offset, setOffset] = useState(0);
  const now = useMemo(() => new Date(), []);
  const from = useMemo(() => {
    const date = new Date(now);
    date.setUTCFullYear(date.getUTCFullYear() - 1);
    return isoDate(date);
  }, [now]);
  const to = useMemo(() => {
    const date = new Date(now);
    date.setUTCDate(date.getUTCDate() + 1);
    return isoDate(date);
  }, [now]);
  const query = useQuery({
    queryKey: ["events", portfolioId, kind, from, to, unitType, unit, offset],
    queryFn: async () => {
      const params = {
        path: { portfolio_id: portfolioId },
        query: {
          from,
          to,
          bucket: "month" as const,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          unit_type: unitType === "all" ? null : unitType,
          unit: unit || null,
          limit: 10,
          offset,
        },
      };
      if (kind === "cash-flows")
        return unwrap(
          await api.GET(
            "/api/v1/portfolios/{portfolio_id}/analytics/cash-flows",
            { params },
          ),
        );
      if (kind === "income")
        return unwrap(
          await api.GET("/api/v1/portfolios/{portfolio_id}/analytics/income", {
            params,
          }),
        );
      if (kind === "costs")
        return unwrap(
          await api.GET("/api/v1/portfolios/{portfolio_id}/analytics/costs", {
            params,
          }),
        );
      return unwrap(
        await api.GET("/api/v1/portfolios/{portfolio_id}/analytics/trading", {
          params,
        }),
      );
    },
  });
  if (query.isLoading || !query.data) return <QueryState error={query.error} />;
  const data: Schema<"EventTimelineResponse"> = query.data;
  const otherDiagnostics = data.diagnostics.filter(
    (item) => item.code !== "historical_reporting_conversion_unavailable",
  );
  const rows = Array.from(
    new Set(
      data.series.flatMap((series) =>
        series.buckets.map((bucket) => bucket.bucket_start),
      ),
    ),
  ).sort();
  const chartRows = rows.map((date) => ({
    date,
    ...Object.fromEntries(
      data.series.map((series) => [
        series.key,
        Number(
          series.buckets.find((bucket) => bucket.bucket_start === date)
            ?.value ?? "0",
        ),
      ]),
    ),
  }));
  return (
    <div className="page events-page">
      <PageHeader
        title="События операций"
        description="Агрегация операций по календарным периодам. Это не график стоимости и не историческая доходность."
      />
      <div className="events-toolbar">
        <div className="tab-list" role="tablist">
          {(Object.keys(labels) as TimelineKind[]).map((item) => (
            <button
              role="tab"
              aria-selected={kind === item}
              key={item}
              onClick={() => {
                setKind(item);
                setOffset(0);
              }}
            >
              {labels[item]}
            </button>
          ))}
        </div>
        <div className="view-switch">
          <button
            aria-pressed={view === "chart"}
            onClick={() => setView("chart")}
          >
            <BarChart3 size={16} /> Диаграмма
          </button>
          <button
            aria-pressed={view === "table"}
            onClick={() => setView("table")}
          >
            <Table2 size={16} /> Таблица
          </button>
        </div>
      </div>
      <div className="event-filters">
        <label>
          Тип единицы
          <select
            value={unitType}
            onChange={(event) => {
              setUnitType(event.target.value as "all" | "currency" | "asset");
              setOffset(0);
            }}
          >
            <option value="all">Все</option>
            <option value="currency">Валюты</option>
            <option value="asset">Активы</option>
          </select>
        </label>
        <label>
          Точная единица
          <input
            value={unit}
            onChange={(event) => {
              setUnit(event.target.value);
              setOffset(0);
            }}
            placeholder="Рубли, доллары США или идентификатор актива"
          />
        </label>
        <span>
          Серии: {data.series_total} · показано {data.series.length}
        </span>
      </div>
      <section className="event-context">
        <CalendarDays />
        <span>
          <small>Период</small>
          <strong>
            {data.period_from} — {data.period_to}
          </strong>
        </span>
        <span>
          <small>Группировка</small>
          <strong>По месяцам · {timeZoneLabel(data.timezone)}</strong>
        </span>
        <span>
          <small>Историческая конвертация</small>
          <strong>События в исходных валютах</strong>
        </span>
        <span>
          <small>Расчёт</small>
          <strong>{formatDate(data.provenance.calculation_as_of)}</strong>
        </span>
      </section>
      {!data.series.length ? (
        <StatePanel title="В периоде нет событий">
          <p>
            Пустое состояние не означает нулевую доходность — в выбранном периоде
            нет операций этого типа.
          </p>
        </StatePanel>
      ) : view === "chart" ? (
        <section className="event-chart">
          <div className="section-heading">
            <div>
              <p className="eyebrow">{labels[kind]}</p>
              <h2>Суммы в исходных единицах</h2>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={360}>
            <BarChart
              data={chartRows}
              margin={{ top: 16, right: 8, left: 4, bottom: 4 }}
            >
              <CartesianGrid vertical={false} stroke="#dedbd2" />
              <XAxis
                dataKey="date"
                tickFormatter={(value) =>
                  new Intl.DateTimeFormat("ru-RU", { month: "short" }).format(
                    new Date(value),
                  )
                }
              />
              <YAxis />
              <Tooltip formatter={(value) => formatDecimal(String(value))} />
              <Legend />
              {data.series.map((series, index) => (
                <Bar
                  key={series.key}
                  dataKey={series.key}
                  name={`${timelineMetricLabel(series.metric)} · ${timelineUnitLabel(series.unit_type, series.unit)}`}
                  fill={colors[index % colors.length]}
                  radius={[3, 3, 0, 0]}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </section>
      ) : (
        <section className="data-section event-table">
          <div className="compact-table">
            <div className="event-row event-row--head">
              <span>Период</span>
              {data.series.map((series) => (
                <span key={series.key}>
                  {timelineMetricLabel(series.metric)}
                  <small>
                    {timelineUnitTypeLabel(series.unit_type)}:{" "}
                    {timelineUnitLabel(series.unit_type, series.unit)}
                  </small>
                </span>
              ))}
            </div>
            {rows.map((date) => (
              <div className="event-row" key={date}>
                <span>
                  <strong>{date}</strong>
                </span>
                {data.series.map((series) => (
                  <span className="numeric" key={series.key}>
                    {formatDecimal(
                      series.buckets.find(
                        (bucket) => bucket.bucket_start === date,
                      )?.value ?? "0",
                    )}
                  </span>
                ))}
              </div>
            ))}
          </div>
        </section>
      )}
      <div className="timeline-diagnostics" role="note">
        <p>{historicalConversionMessage}</p>
        {otherDiagnostics.map((item) => (
          <div key={`${item.code}-${item.message}`}>
            <p>{diagnosticMessage(item.code, item.message)}</p>
          </div>
        ))}
      </div>
      {data.series_total > data.limit ? (
        <div className="series-pagination">
          <Button
            variant="secondary"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - data.limit))}
          >
            Назад
          </Button>
          <span>
            {offset + 1}–{Math.min(offset + data.limit, data.series_total)} из{" "}
            {data.series_total}
          </span>
          <Button
            variant="secondary"
            disabled={offset + data.limit >= data.series_total}
            onClick={() => setOffset(offset + data.limit)}
          >
            Далее
          </Button>
        </div>
      ) : null}
    </div>
  );
}
