import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertOctagon,
  Clock3,
  DatabaseZap,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useWorkspace } from "../app/workspace-context";
import { Button, PageHeader, QueryState, StatePanel } from "../components/ui";
import {
  currencyLabel,
  diagnosticCountLabel,
  diagnosticExplanation,
  diagnosticMessage,
  formatDate,
  formatDecimal,
  impactLabel,
  userFacingName,
} from "../lib/format";

function diagnosticKind(code: string) {
  if (code.includes("stale")) return "stale";
  if (code.includes("missing") || code.includes("unavailable"))
    return "unavailable";
  return "partial";
}

export function DataQualityPage() {
  const { portfolioId = "" } = useParams();
  const { currency } = useWorkspace();
  const queryClient = useQueryClient();
  const query = useQuery({
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
  });
  const instruments = useQuery({
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
  });
  const [instrumentId, setInstrumentId] = useState("");
  const [price, setPrice] = useState("");
  const [priceCurrency, setPriceCurrency] = useState("");
  const [fxBase, setFxBase] = useState("USD");
  const [fxQuote, setFxQuote] = useState("RUB");
  const [rate, setRate] = useState("");
  const priceableInstruments = [
    ...new Map(
      (instruments.data?.items ?? [])
        .filter((item) => item.instrument_id)
        .map((item) => [item.instrument_id, item]),
    ).values(),
  ];
  const selectedInstrument =
    priceableInstruments.find((item) => item.instrument_id === instrumentId) ??
    priceableInstruments[0];
  const refresh = async () => {
    unwrap(
      await api.POST(
        "/api/v1/portfolios/{portfolio_id}/positions/recalculate",
        {
          params: { path: { portfolio_id: portfolioId } },
          body: { cost_basis_method: "weighted_average" },
        },
      ),
    );
    await queryClient.invalidateQueries();
  };
  const priceMutation = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/prices/batch", {
          body: {
            items: [
              {
                instrument_id: selectedInstrument?.instrument_id || "",
                price,
                currency:
                  priceCurrency || selectedInstrument?.source_currency || "RUB",
                observed_at: new Date().toISOString(),
              },
            ],
          },
        }),
      ),
    onSuccess: refresh,
  });
  const fxMutation = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/fx-rates/batch", {
          body: {
            items: [
              {
                base_currency: fxBase,
                quote_currency: fxQuote,
                rate,
                observed_at: new Date().toISOString(),
              },
            ],
          },
        }),
      ),
    onSuccess: refresh,
  });
  const fxSync = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/fx-rates/sync")),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["fx-sync-latest"] });
    },
  });
  const fxSyncJob = useQuery({
    queryKey: ["fx-sync-latest"],
    queryFn: async () => unwrap(await api.GET("/api/v1/fx-rates/sync")),
    refetchInterval: (job) =>
      ["pending", "running"].includes(job.state.data?.status ?? "")
        ? 1_200
        : 30_000,
  });
  const currentFx = useQuery({
    queryKey: [
      "current-usd-rub",
      fxSyncJob.data?.status,
      fxSyncJob.data?.updated_at,
    ],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/fx-rates/resolve", {
          params: { query: { base_currency: "USD", quote_currency: "RUB" } },
        }),
      ),
    refetchInterval: 30_000,
  });
  const recalc = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST(
          "/api/v1/portfolios/{portfolio_id}/positions/recalculate",
          {
            params: { path: { portfolio_id: portfolioId } },
            body: { cost_basis_method: "weighted_average" },
          },
        ),
      ),
    onSuccess: async () => queryClient.invalidateQueries(),
  });
  if (query.isLoading || !query.data) return <QueryState error={query.error} />;
  const data = query.data;
  return (
    <div className="page quality-page">
      <PageHeader
        title="Качество и готовность данных"
        description="Здесь видно, каких сохранённых цен, курсов или данных об операциях не хватает. Проверенные криптоактивы получают глобальную цену USD автоматически; для остальных активов доступен ручной ввод. Курс USD/RUB обновляется из Банка России."
        actions={
          <Button
            variant="secondary"
            onClick={() => recalc.mutate()}
            disabled={recalc.isPending}
          >
            <RefreshCw size={16} /> Пересчитать
          </Button>
        }
      />
      <section className="quality-provenance">
        <div>
          <ShieldCheck />
          <span>
            <small>Контракт расчёта</small>
            <strong>
              Версия {data.provenance.calculation_contract_version}
            </strong>
          </span>
        </div>
        <div>
          <DatabaseZap />
          <span>
            <small>Дата расчёта</small>
            <strong>{formatDate(data.provenance.snapshot_as_of)}</strong>
          </span>
        </div>
        <div>
          <Clock3 />
          <span>
            <small>Наблюдений цен / курсов</small>
            <strong>
              {data.provenance.used_market_price_observation_count} /{" "}
              {data.provenance.used_fx_observation_count}
            </strong>
          </span>
        </div>
      </section>
      {!data.diagnostics.length ? (
        <StatePanel title="Данные готовы">
          <p>
            Система не обнаружила предупреждений или ошибок. У каждого
            показателя по-прежнему указано собственное состояние качества.
          </p>
        </StatePanel>
      ) : (
        <section className="diagnostic-list">
          {data.diagnostics.map((item) => (
            <article
              className={`diagnostic diagnostic--${diagnosticKind(item.code)}`}
              key={`${item.code}-${item.message}`}
            >
              <div className="diagnostic__icon">
                <AlertOctagon />
              </div>
              <div>
                <div className="diagnostic__title">
                  <h2>{diagnosticMessage(item.code, item.message)}</h2>
                  <span>{diagnosticCountLabel(item.code, item.count)}</span>
                </div>
                <p>{diagnosticExplanation(item.code)}</p>
                <p className="diagnostic__impacts">
                  <strong>Влияние:</strong>{" "}
                  {item.impacts
                    .map((impact) =>
                      impactLabel(impact.metric, impact.state, impact.endpoint),
                    )
                    .join(" · ") || "Влияние ограничено источником данных"}
                </p>
              </div>
            </article>
          ))}
        </section>
      )}
      <section className="corrective-grid">
        <form
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            priceMutation.mutate();
          }}
          className="action-panel"
        >
          <h2>Добавить рыночную цену</h2>
          <p>
            Цены инструментов не обновляются автоматически. Сохраните рыночную
            цену вручную — после сохранения портфель пересчитается.
            Автоматически обновляется только поддерживаемый курс доллара США к
            российскому рублю из Банка России.
          </p>
          <label>
            Инструмент
            <select
              value={selectedInstrument?.instrument_id || ""}
              onChange={(e) => {
                setInstrumentId(e.target.value);
                setPriceCurrency("");
                setPrice("");
              }}
            >
              {!priceableInstruments.length ? (
                <option value="">Нет позиций для оценки</option>
              ) : null}
              {priceableInstruments.map((item) => (
                <option key={item.instrument_id} value={item.instrument_id!}>
                  {userFacingName(item.instrument_name)} ·{" "}
                  {currencyLabel(item.source_currency)}
                </option>
              ))}
            </select>
          </label>
          <div className="field-row">
            <label>
              Цена
              <input
                inputMode="decimal"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder="125.50"
                required
              />
            </label>
            <label>
              Валюта
              <input
                value={
                  priceCurrency || selectedInstrument?.source_currency || "RUB"
                }
                onChange={(e) => setPriceCurrency(e.target.value.toUpperCase())}
                maxLength={3}
                required
              />
            </label>
          </div>
          <Button
            disabled={priceMutation.isPending || !priceableInstruments.length}
          >
            Сохранить цену
          </Button>
        </form>
        <details className="action-panel manual-fx-panel">
          <summary>Ввести другой курс вручную</summary>
          <form
            onSubmit={(event: FormEvent) => {
              event.preventDefault();
              fxMutation.mutate();
            }}
          >
            <p>
              Резервный способ для других валют или недоступности источника ЦБ.
              Обычно вводить курс доллара США к российскому рублю вручную не
              требуется.
            </p>
            <div className="field-row">
              <label>
                Базовая
                <input
                  value={fxBase}
                  onChange={(e) => setFxBase(e.target.value.toUpperCase())}
                  maxLength={3}
                  required
                />
              </label>
              <label>
                Котируемая
                <input
                  value={fxQuote}
                  onChange={(e) => setFxQuote(e.target.value.toUpperCase())}
                  maxLength={3}
                  required
                />
              </label>
            </div>
            <label>
              Курс
              <input
                inputMode="decimal"
                value={rate}
                onChange={(e) => setRate(e.target.value)}
                placeholder="92.450000"
                required
              />
            </label>
            <Button disabled={fxMutation.isPending}>Сохранить курс</Button>
          </form>
        </details>
        <section className="action-panel">
          <h2>Курс доллара · Банк России</h2>
          <p>
            Фоновый обработчик проверяет официальный дневной курс доллара США к
            российскому рублю каждый час. Доступный курс показан ниже; для его
            применения к старому расчёту нажмите «Пересчитать».
          </p>
          <p role="status">
            {currentFx.data?.rate
              ? `1 $ = ${formatDecimal(currentFx.data.rate, 4)} ₽`
              : "Курс пока не получен"}
            <br />
            {currentFx.data?.observations[0]
              ? `${currentFx.data.observations[0].mode === "automatic" ? "Банк России" : "Ручной ввод"} · ${formatDate(currentFx.data.observations[0].observed_at)}`
              : null}
            {currentFx.data?.status === "stale" ? " · Устарел" : null}
          </p>
          <div className="sync-status">
            <span>Последнее обновление</span>
            <strong>
              {
                {
                  pending: "Ожидает обработки",
                  running: "Получаем курс…",
                  succeeded: "Курс получен",
                  failed: "Источник недоступен — повторим автоматически",
                  cancelled: "Отменено",
                }[fxSyncJob.data?.status ?? "pending"]
              }
            </strong>
            {fxSyncJob.data ? (
              <small>{formatDate(fxSyncJob.data.updated_at)}</small>
            ) : (
              <small>Нет сохранённых заданий</small>
            )}
          </div>
          <Button
            variant="secondary"
            onClick={() => fxSync.mutate()}
            disabled={
              fxSync.isPending ||
              ["pending", "running"].includes(fxSyncJob.data?.status ?? "")
            }
          >
            Обновить курс сейчас
          </Button>
        </section>
      </section>
      {priceMutation.error ||
      fxMutation.error ||
      fxSync.error ||
      recalc.error ? (
        <p className="form-error">
          {
            (
              priceMutation.error ??
              fxMutation.error ??
              fxSync.error ??
              recalc.error
            )?.message
          }
        </p>
      ) : null}
    </div>
  );
}
