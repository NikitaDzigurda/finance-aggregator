import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Save, SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useWorkspace } from "../app/workspace-context";
import {
  Button,
  PageHeader,
  QualityBadge,
  QueryState,
  StatePanel,
} from "../components/ui";
import {
  accountTypeLabel,
  assetClassLabel,
  breakdownDimensionLabel,
  currencyLabel,
  diagnosticMessage,
  formatMoney,
  formatPercent,
  userFacingName,
} from "../lib/format";

const palette = [
  "#007a77",
  "#28a7a5",
  "#63b7e6",
  "#7189ad",
  "#c2c8d1",
  "#356aa8",
  "#8fa5c4",
];

export function AllocationPage() {
  const { portfolioId = "" } = useParams();
  const { currency } = useWorkspace();
  const queryClient = useQueryClient();
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
  });
  const categories = useQuery({
    queryKey: ["categories", portfolioId],
    queryFn: async () =>
      unwrap(
        await api.GET(
          "/api/v1/portfolios/{portfolio_id}/allocation/categories",
          { params: { path: { portfolio_id: portfolioId } } },
        ),
      ),
  });
  const breakdown = useQuery({
    queryKey: ["breakdown", portfolioId, currency],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/portfolios/{portfolio_id}/analytics/breakdown", {
          params: {
            path: { portfolio_id: portfolioId },
            query: { reporting_currency: currency },
          },
        }),
      ),
  });
  const exposure = useQuery({
    queryKey: ["exposure", portfolioId, currency],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/portfolios/{portfolio_id}/analytics/exposure", {
          params: {
            path: { portfolio_id: portfolioId },
            query: { reporting_currency: currency },
          },
        }),
      ),
  });
  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/instruments", {
          params: { query: { limit: 100 } },
        }),
      ),
  });
  const [overrideInstrument, setOverrideInstrument] = useState("");
  const [overrideCategory, setOverrideCategory] = useState("");
  const assignment = useQuery({
    queryKey: ["instrument-category", portfolioId, overrideInstrument],
    enabled: Boolean(overrideInstrument),
    queryFn: async () =>
      unwrap(
        await api.GET(
          "/api/v1/portfolios/{portfolio_id}/allocation/instrument-categories/{instrument_id}",
          {
            params: {
              path: {
                portfolio_id: portfolioId,
                instrument_id: overrideInstrument,
              },
            },
          },
        ),
      ),
  });
  const assignCategory = useMutation({
    mutationFn: async () => {
      const categoryId = overrideCategory || assignment.data?.category.id;
      if (!overrideInstrument || !categoryId)
        throw new Error("Выберите инструмент и категорию");
      return unwrap(
        await api.PUT(
          "/api/v1/portfolios/{portfolio_id}/allocation/instrument-categories/{instrument_id}",
          {
            params: {
              path: {
                portfolio_id: portfolioId,
                instrument_id: overrideInstrument,
              },
            },
            body: { category_id: categoryId },
          },
        ),
      );
    },
    onSuccess: async () => {
      setOverrideCategory("");
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["instrument-category", portfolioId, overrideInstrument],
        }),
        queryClient.invalidateQueries({
          queryKey: ["allocation", portfolioId],
        }),
        queryClient.invalidateQueries({ queryKey: ["holdings", portfolioId] }),
      ]);
    },
  });
  const clearCategory = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.DELETE(
          "/api/v1/portfolios/{portfolio_id}/allocation/instrument-categories/{instrument_id}",
          {
            params: {
              path: {
                portfolio_id: portfolioId,
                instrument_id: overrideInstrument,
              },
            },
          },
        ),
      ),
    onSuccess: async () => {
      setOverrideCategory("");
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["instrument-category", portfolioId, overrideInstrument],
        }),
        queryClient.invalidateQueries({
          queryKey: ["allocation", portfolioId],
        }),
        queryClient.invalidateQueries({ queryKey: ["holdings", portfolioId] }),
      ]);
    },
  });
  if (
    allocation.isLoading ||
    categories.isLoading ||
    !allocation.data ||
    !categories.data
  )
    return <QueryState error={allocation.error ?? categories.error} />;
  const chartData = allocation.data.items
    .filter((item) => item.actual_weight !== null)
    .map((item) => ({
      name: assetClassLabel(item.category.system_class, item.category.name),
      value: Number(item.actual_weight),
    }));
  const hasNegativeAllocation = allocation.data.diagnostics.some(
    (item) => item.code === "allocation_negative_component",
  );
  const canRenderAllocationChart =
    chartData.length > 0 && !hasNegativeAllocation;
  return (
    <div className="page allocation-page">
      <PageHeader
        title="Распределение"
        description="Фактические стоимость и доли рассчитаны по известным компонентам портфеля."
        actions={<QualityBadge state={allocation.data.quality} />}
      />
      {!allocation.data.items.length ? (
        <StatePanel title="Распределение пока недоступно">
          <p>Нужны рассчитанные позиции и известные рыночные стоимости.</p>
        </StatePanel>
      ) : (
        <section className="allocation-hero">
          <div className="donut-wrap">
            {canRenderAllocationChart ? (
              <>
                <ResponsiveContainer width="100%" height={310}>
                  <PieChart data-testid="allocation-donut">
                    <Pie
                      data={chartData}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={83}
                      outerRadius={127}
                      paddingAngle={1.5}
                      stroke="none"
                    >
                      {chartData.map((_, index) => (
                        <Cell
                          key={index}
                          fill={palette[index % palette.length]}
                        />
                      ))}
                    </Pie>
                    <Tooltip
                      formatter={(value) => formatPercent(String(value))}
                    />
                  </PieChart>
                </ResponsiveContainer>
                <div className="donut-center">
                  <small>Известно</small>
                  <strong>
                    {formatMoney(
                      allocation.data.denominator,
                      allocation.data.reporting_currency,
                    )}
                  </strong>
                  <span>
                    Учтено: {allocation.data.included_components} · исключено:{" "}
                    {allocation.data.excluded_components}
                  </span>
                </div>
              </>
            ) : (
              <div className="allocation-chart-note" role="note">
                <strong>Диаграмма неприменима</strong>
                <span>
                  Отрицательный денежный остаток нельзя корректно показать
                  секторами. Используйте точные суммы и доли в таблице.
                </span>
              </div>
            )}
          </div>
          <div className="allocation-list">
            <p className="eyebrow">Фактическая структура</p>
            {allocation.data.items.map((item, index) => (
              <div key={item.category.id}>
                <i style={{ background: palette[index % palette.length] }} />
                <span>
                  <strong>
                    {assetClassLabel(
                      item.category.system_class,
                      item.category.name,
                    )}
                  </strong>
                  <small>
                    {item.category.is_system
                      ? "системная категория"
                      : "пользовательская категория"}
                  </small>
                </span>
                <span className="numeric">
                  <strong>
                    {formatMoney(
                      item.known_value,
                      allocation.data.reporting_currency,
                    )}
                  </strong>
                  <small>стоимость</small>
                </span>
                <span className="numeric">
                  <strong>{formatPercent(item.actual_weight)}</strong>
                  <small>фактическая доля</small>
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
      {allocation.data.diagnostics.length ? (
        <section className="data-section allocation-diagnostics">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Покрытие расчёта</p>
              <h2>Ограничения и качество расчёта</h2>
              <p>
                Здесь показано, какие данные не вошли в оценку или требуют
                внимания при чтении фактической структуры.
              </p>
            </div>
          </div>
          <div className="allocation-diagnostic-list">
            {allocation.data.diagnostics.map((item) => (
              <div key={`${item.code}-${item.message}`}>
                <strong>{diagnosticMessage(item.code, item.message)}</strong>
              </div>
            ))}
          </div>
        </section>
      ) : null}
      <section className="category-override data-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Классификация инструмента</p>
            <h2>Категория инструмента</h2>
            <p>
              Явное назначение действует только в этом портфеле. Сброс
              возвращает системную категорию по типу инструмента.
            </p>
          </div>
          <SlidersHorizontal size={22} aria-hidden="true" />
        </div>
        <div className="override-controls">
          <label>
            <span>Инструмент</span>
            <select
              aria-label="Инструмент для категории"
              value={overrideInstrument}
              onChange={(event) => {
                setOverrideInstrument(event.target.value);
                setOverrideCategory("");
              }}
            >
              <option value="">Выберите инструмент</option>
              {instruments.data?.items.map((instrument) => (
                <option key={instrument.id} value={instrument.id}>
                  {userFacingName(instrument.name)} ·{" "}
                  {currencyLabel(instrument.currency)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Категория</span>
            <select
              aria-label="Категория инструмента"
              value={overrideCategory || assignment.data?.category.id || ""}
              disabled={!overrideInstrument || assignment.isLoading}
              onChange={(event) => setOverrideCategory(event.target.value)}
            >
              <option value="">Выберите категорию</option>
              {categories.data.items.map((category) => (
                <option key={category.id} value={category.id}>
                  {userFacingName(category.name)} ·{" "}
                  {category.is_system ? "системная" : "пользовательская"}
                </option>
              ))}
            </select>
          </label>
          <div className="override-actions">
            <Button
              onClick={() => assignCategory.mutate()}
              disabled={
                !overrideInstrument ||
                assignment.isLoading ||
                assignCategory.isPending
              }
            >
              <Save size={16} /> Назначить
            </Button>
            <Button
              variant="secondary"
              onClick={() => clearCategory.mutate()}
              disabled={
                assignment.data?.source !== "override" ||
                clearCategory.isPending
              }
            >
              <RotateCcw size={16} /> Вернуть системную
            </Button>
          </div>
        </div>
        {overrideInstrument && assignment.data ? (
          <p className="override-status">
            Текущая категория:{" "}
            <strong>{userFacingName(assignment.data.category.name)}</strong>{" "}
            · источник:{" "}
            {assignment.data.source === "override"
              ? "назначена вручную"
              : "определена системой"}
          </p>
        ) : null}
        {assignment.error || assignCategory.error || clearCategory.error ? (
          <p className="form-error">
            {
              (assignment.error ?? assignCategory.error ?? clearCategory.error)
                ?.message
            }
          </p>
        ) : null}
      </section>
      <section className="structure-grid">
        <article className="data-section structure-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Проекции</p>
              <h2>Разрезы портфеля</h2>
            </div>
          </div>
          {breakdown.data?.dimensions.slice(0, 3).map((dimension) => (
            <div className="structure-group" key={dimension.dimension}>
              <div>
                <strong>{breakdownDimensionLabel(dimension.dimension)}</strong>
                <QualityBadge state={dimension.quality} />
              </div>
              {dimension.items.slice(0, 5).map((item) => (
                <p key={item.key}>
                  <span>{userFacingName(item.label)}</span>
                  <span className="numeric">
                    {formatMoney(
                      item.known_value,
                      breakdown.data.reporting_currency,
                    )}
                    <small>{formatPercent(item.weight)}</small>
                  </span>
                </p>
              ))}
            </div>
          )) ?? <p className="muted">Разрезы загружаются…</p>}
        </article>
        <article className="data-section structure-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Контрагенты</p>
              <h2>По брокерам и криптобиржам</h2>
            </div>
            {exposure.data ? (
              <QualityBadge state={exposure.data.quality} />
            ) : null}
          </div>
          {exposure.data?.items.length ? (
            exposure.data.items.map((item) => (
              <div className="exposure-row" key={item.key}>
                <span>
                  <strong>{userFacingName(item.institution_name)}</strong>
                  <small>
                    {item.account_types.map(accountTypeLabel).join(" / ")} ·
                    счетов: {item.account_count}
                  </small>
                </span>
                <span className="numeric">
                  <strong>
                    {formatMoney(
                      item.known_value,
                      exposure.data.reporting_currency,
                    )}
                  </strong>
                  <small>{formatPercent(item.weight)}</small>
                </span>
              </div>
            ))
          ) : (
            <p className="muted">
              Нет оценённых данных по брокерам и криптобиржам.
            </p>
          )}
        </article>
      </section>
    </div>
  );
}
