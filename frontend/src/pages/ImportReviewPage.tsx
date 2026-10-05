import { Dialog } from "@base-ui/react/dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  LoaderCircle,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Virtuoso } from "react-virtuoso";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, unwrap, type Schema } from "../api/client";
import {
  Button,
  ImportStepper,
  PageHeader,
  QualityBadge,
  QueryState,
  StatePanel,
} from "../components/ui";
import {
  currencyLabel,
  formatDate,
  humanStatus,
  operationTypeLabel,
  sourceProviderLabel,
  userFacingName,
} from "../lib/format";

const processingStatuses = new Set([
  "uploaded",
  "detecting",
  "parsing",
  "committing",
  "recalculating",
]);
const reviewStatuses = new Set([
  "awaiting_review",
  "ready_to_commit",
  "committed",
  "completed",
  "rolled_back",
]);

function candidateText(candidate: Record<string, unknown> | null): {
  title: string;
  meta: string;
} {
  if (!candidate)
    return {
      title: "Строка не нормализована",
      meta: "Кандидат операции отсутствует",
    };
  const payload = (candidate.payload ?? {}) as Record<string, unknown>;
  const title = operationTypeLabel(String(candidate.operation_type ?? ""));
  const pieces = [
    payload.side === "buy"
      ? "покупка"
      : payload.side === "sell"
        ? "продажа"
        : payload.side,
    payload.quantity,
    payload.price,
    payload.amount,
    payload.currency,
    payload.price_currency,
  ].filter(Boolean);
  return {
    title,
    meta:
      pieces.map(String).join(" · ") ||
      "Точные значения сохранены до подтверждения",
  };
}

function importFailure(
  summary: Record<string, unknown> | null,
  sourceProvider: string,
): string {
  const code = String(summary?.code ?? "");
  const messages: Record<string, string> = {
    alfa_xml_variant_unsupported:
      "Выбран обычный XML-отчёт Альфа‑Инвестиций. Экспортируйте вариант «XML для импорта» и загрузите его без изменений.",
    import_adapter_not_found:
      sourceProvider === "alfa_broker_xml_import"
        ? "Файл не соответствует XML «для импорта» Альфа‑Инвестиций. В меню сохранения отчёта выберите именно «XML для импорта», а не обычный XML."
        : "Файл не соответствует выбранному источнику. Выберите банк или биржу, которые сформировали этот отчёт.",
    import_account_type_mismatch:
      "Для брокерского отчёта нужен брокерский счёт, а для Байбит — счёт криптобиржи.",
    import_file_count_invalid:
      "Для Байбит выберите четыре CSV из одного экспорта; для брокера — один отчёт.",
  };
  return (
    messages[code] ??
    "Файл не удалось обработать. Проверьте источник и повторите загрузку."
  );
}

export function ImportReviewPage() {
  const { portfolioId = "", batchId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [matches, setMatches] = useState<Record<string, string>>({});
  const statusQuery = useQuery({
    queryKey: ["import", batchId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/imports/{batch_id}", {
          params: { path: { batch_id: batchId } },
        }),
      ),
    refetchInterval: (query) =>
      processingStatuses.has(query.state.data?.batch.status ?? "")
        ? 1200
        : false,
  });
  const status = statusQuery.data?.batch.status;
  const preview = useQuery({
    queryKey: ["import-preview", batchId],
    enabled: Boolean(status && reviewStatuses.has(status)),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/imports/{batch_id}/preview", {
          params: { path: { batch_id: batchId }, query: { limit: 200 } },
        }),
      ),
  });
  const instruments = useQuery({
    queryKey: ["instruments"],
    enabled: status === "awaiting_review",
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/instruments", {
          params: { query: { limit: 100 } },
        }),
      ),
  });
  const resolve = useMutation({
    mutationFn: async ({
      rowId,
      body,
    }: {
      rowId: string;
      body: Schema<"ImportRowResolutionRequest">;
    }) =>
      unwrap(
        await api.PATCH("/api/v1/imports/{batch_id}/rows/{row_id}", {
          params: { path: { batch_id: batchId, row_id: rowId } },
          body,
        }),
      ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["import", batchId] }),
        queryClient.invalidateQueries({
          queryKey: ["import-preview", batchId],
        }),
      ]);
    },
  });
  const confirm = useMutation({
    mutationFn: async () => {
      const result = unwrap(
        await api.POST("/api/v1/imports/{batch_id}/confirm", {
          params: { path: { batch_id: batchId } },
        }),
      );
      await api.POST(
        "/api/v1/portfolios/{portfolio_id}/positions/recalculate",
        {
          params: { path: { portfolio_id: portfolioId } },
          body: { cost_basis_method: "weighted_average" },
        },
      );
      return result;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries();
      navigate(`/p/${portfolioId}/overview`);
    },
  });
  const rollback = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/imports/{batch_id}/rollback", {
          params: { path: { batch_id: batchId } },
        }),
      ),
    onSuccess: async () => {
      await api.POST(
        "/api/v1/portfolios/{portfolio_id}/positions/recalculate",
        {
          params: { path: { portfolio_id: portfolioId } },
          body: { cost_basis_method: "weighted_average" },
        },
      );
      await queryClient.invalidateQueries();
    },
  });
  const unresolved = useMemo(
    () =>
      preview.data?.items.filter((row) =>
        ["warning", "error", "duplicate"].includes(row.status),
      ).length ?? 0,
    [preview.data],
  );
  const actionRows = useMemo(
    () =>
      preview.data?.items.filter((row) =>
        ["warning", "error", "duplicate"].includes(row.status),
      ) ?? [],
    [preview.data],
  );
  const [delayedBatchId, setDelayedBatchId] = useState<string | null>(null);
  const polledBatch = statusQuery.data?.batch;
  const polledParseJob = statusQuery.data?.jobs.find(
    (job) => job.job_type === "parse_import",
  );
  useEffect(() => {
    if (
      polledBatch?.status !== "uploaded" ||
      polledParseJob?.status !== "pending"
    )
      return;
    const elapsed = Date.now() - Date.parse(polledParseJob.created_at);
    const timeout = window.setTimeout(
      () => setDelayedBatchId(polledBatch.id),
      Math.max(0, 30_000 - elapsed),
    );
    return () => window.clearTimeout(timeout);
  }, [polledBatch?.id, polledBatch?.status, polledParseJob]);
  if (statusQuery.isLoading || !statusQuery.data)
    return <QueryState error={statusQuery.error} />;
  const batch = statusQuery.data.batch;
  const parseJob = statusQuery.data.jobs.find(
    (job) => job.job_type === "parse_import",
  );
  const processingDelayed =
    batch.status === "uploaded" &&
    parseJob?.status === "pending" &&
    delayedBatchId === batch.id;
  if (processingDelayed)
    return (
      <div className="page">
        <PageHeader
          title="Обработка отчёта задерживается"
          description={batch.original_filename}
        />
        <ImportStepper active={3} />
        <StatePanel kind="error" title="Сервис обработки не отвечает">
          <p>
            Файл сохранён, но фоновый обработчик ещё не забрал задание. Запустите
            локальный фоновый обработчик, затем обновите эту страницу.
          </p>
          <Button type="button" onClick={() => statusQuery.refetch()}>
            Проверить снова
          </Button>
        </StatePanel>
      </div>
    );
  if (processingStatuses.has(batch.status))
    return (
      <div className="page processing-page">
        <PageHeader
          title="Обрабатываем отчёт"
          description={batch.original_filename}
        />
        <ImportStepper active={3} />
        <section className="processing-card">
          <LoaderCircle className="spin" />
          <h2>{humanStatus(batch.status)}</h2>
          <p>
            Серверная часть определяет формат, сохраняет трассируемые строки и
            готовит предпросмотр. Страница обновится автоматически.
          </p>
          <div className="progress-track">
            <span />
          </div>
          <small>
            Файл: {batch.files.length} ·{" "}
            {Math.round(batch.file_size_bytes / 1024)} КБ
          </small>
        </section>
      </div>
    );
  if (batch.status === "failed")
    return (
      <div className="page">
        <PageHeader title="Импорт не обработан" />
        <StatePanel kind="error" title="Не удалось распознать отчёт">
          <p>{importFailure(batch.error_summary, batch.source_provider)}</p>
          <Link to={`/p/${portfolioId}/imports/new`}>
            Выбрать другой источник
          </Link>
        </StatePanel>
      </div>
    );
  if (preview.isLoading || !preview.data)
    return <QueryState error={preview.error} />;
  return (
    <div className="page import-review-page">
      <PageHeader
        title="Проверка импорта"
        description={`${batch.original_filename} · ${sourceProviderLabel(batch.source_provider)} · ${formatDate(batch.created_at)}`}
        actions={
          <Link className="back-link" to={`/p/${portfolioId}/imports`}>
            <ArrowLeft size={16} /> История
          </Link>
        }
      />
      <ImportStepper active={5} />
      <section className="review-summary">
        <div>
          <span>Всего строк</span>
          <strong>{preview.data.total_rows}</strong>
        </div>
        <div className="ready">
          <span>Готово</span>
          <strong>{preview.data.ready_rows}</strong>
        </div>
        <div className="warning">
          <span>Предупреждения</span>
          <strong>{preview.data.warning_rows}</strong>
        </div>
        <div className="duplicate">
          <span>Дубликаты</span>
          <strong>{preview.data.duplicate_rows}</strong>
        </div>
        <div className="error">
          <span>Ошибки</span>
          <strong>{preview.data.error_rows}</strong>
        </div>
        <div>
          <span>Исключено</span>
          <strong>{preview.data.excluded_rows}</strong>
        </div>
      </section>
      {unresolved ? (
        <div className="inline-notice inline-notice--warning">
          <AlertTriangle />
          <div>
            <strong>Нужно принять {unresolved} решений</strong>
            <p>
              Строки с предупреждениями, ошибками и дубликатами не будут
              записаны без явного действия.
            </p>
            <Link
              to={`/p/${portfolioId}/instruments?return=${encodeURIComponent(location.pathname)}`}
            >
              Создать или найти инструмент
            </Link>
          </div>
        </div>
      ) : (
        <div className="inline-notice inline-notice--success">
          <ShieldCheck />
          <div>
            <strong>Проверка завершена</strong>
            <p>Все включённые строки готовы к атомарному подтверждению.</p>
          </div>
        </div>
      )}
      {actionRows.length ? (
        <section className="data-section review-table-section">
          <div className="vrow review-row review-row--head">
            <span>#</span>
            <span>Кандидат операции</span>
            <span>Исходная строка</span>
            <span>Состояние</span>
            <span>Решение</span>
          </div>
          <Virtuoso
            style={{
              height: Math.min(620, Math.max(180, actionRows.length * 82)),
            }}
            data={actionRows}
            itemContent={(_, row) => {
              const copy = candidateText(row.normalized_candidate);
              const needsInstrumentMatch = [
                ...row.warnings,
                ...row.errors,
              ].some(
                (diagnostic) => diagnostic.code === "instrument_match_required",
              );
              const selectedInstrument =
                matches[row.id] ?? instruments.data?.items[0]?.id ?? "";
              return (
                <div className="vrow review-row">
                  <span className="numeric">{row.sequence_number}</span>
                  <span>
                    <strong>{copy.title}</strong>
                    <small>{copy.meta}</small>
                  </span>
                  <span>
                    <strong>
                      {row.source_sheet ?? "Отчёт"}
                      {row.source_row_number
                        ? ` · строка ${row.source_row_number}`
                        : ""}
                    </strong>
                    <small>
                      {[...row.warnings, ...row.errors]
                        .map((item) =>
                          String(item.message ?? item.code ?? "Диагностика"),
                        )
                        .join("; ")}
                    </small>
                  </span>
                  <span>
                    <QualityBadge
                      state={
                        row.status === "committed" ? "completed" : row.status
                      }
                    />
                  </span>
                  <span className="row-actions">
                    {needsInstrumentMatch && instruments.data?.items.length ? (
                      <>
                        <select
                          aria-label={`Инструмент для строки ${row.sequence_number}`}
                          value={selectedInstrument}
                          onChange={(e) =>
                            setMatches((current) => ({
                              ...current,
                              [row.id]: e.target.value,
                            }))
                          }
                        >
                          {instruments.data.items.map((item) => (
                            <option key={item.id} value={item.id}>
                              {userFacingName(item.name)} ·{" "}
                              {currencyLabel(item.currency)}
                            </option>
                          ))}
                        </select>
                        <Button
                          variant="secondary"
                          disabled={resolve.isPending}
                          onClick={() =>
                            resolve.mutate({
                              rowId: row.id,
                              body: {
                                action: "match_instrument",
                                instrument_id: selectedInstrument,
                                target: "instrument",
                                note: "Проверено в предпросмотре",
                              },
                            })
                          }
                        >
                          Сопоставить
                        </Button>
                      </>
                    ) : null}
                    {row.status === "duplicate" ? (
                      <Button
                        variant="secondary"
                        disabled={resolve.isPending}
                        onClick={() =>
                          resolve.mutate({
                            rowId: row.id,
                            body: {
                              action: "allow_duplicate",
                              note: "Явно подтверждено в предпросмотре",
                            },
                          })
                        }
                      >
                        Принять дубликат
                      </Button>
                    ) : null}
                    {["warning", "error", "duplicate"].includes(row.status) ? (
                      <Button
                        variant="ghost"
                        disabled={resolve.isPending}
                        onClick={() =>
                          resolve.mutate({
                            rowId: row.id,
                            body: {
                              action: "exclude",
                              note: "Исключено при проверке",
                            },
                          })
                        }
                      >
                        Исключить
                      </Button>
                    ) : null}
                  </span>
                </div>
              );
            }}
          />
        </section>
      ) : (
        <section className="inline-notice inline-notice--success">
          <ShieldCheck />
          <div>
            <strong>Отчёт обработан автоматически</strong>
            <p>
              К подтверждению подготовлено {preview.data.ready_rows} операций.
              {preview.data.excluded_rows
                ? ` ${preview.data.excluded_rows} технических и сверочных строк не создают повторных операций.`
                : ""}
            </p>
          </div>
        </section>
      )}
      <footer className="review-footer">
        <div>
          <strong>
            {batch.status === "ready_to_commit"
              ? "Готово к подтверждению"
              : humanStatus(batch.status)}
          </strong>
          <small>
            Журнал операций изменится только после нажатия кнопки.
          </small>
        </div>
        <div className="review-footer__actions">
          {["committed", "completed"].includes(batch.status) ? (
            <Dialog.Root>
              <Dialog.Trigger className="button button--danger">
                <RotateCcw size={16} /> Откатить импорт
              </Dialog.Trigger>
              <Dialog.Portal>
                <Dialog.Backdrop className="dialog-backdrop" />
                <Dialog.Viewport className="dialog-viewport">
                  <Dialog.Popup className="dialog">
                    <Dialog.Title>Откатить подтверждённый импорт?</Dialog.Title>
                    <Dialog.Description>
                      Будут удалены только операции этого импорта. Действие
                      идемпотентно; затем портфель пересчитается.
                    </Dialog.Description>
                    <div className="dialog__actions">
                      <Dialog.Close className="button button--secondary">
                        Отмена
                      </Dialog.Close>
                      <Button
                        variant="danger"
                        onClick={() => rollback.mutate()}
                        disabled={rollback.isPending}
                      >
                        {rollback.isPending ? "Откат…" : "Откатить"}
                      </Button>
                    </div>
                  </Dialog.Popup>
                </Dialog.Viewport>
              </Dialog.Portal>
            </Dialog.Root>
          ) : null}
          {batch.status === "ready_to_commit" ? (
            <Button
              onClick={() => confirm.mutate()}
              disabled={confirm.isPending}
            >
              <CheckCircle2 size={17} />{" "}
              {confirm.isPending
                ? "Подтверждаем…"
                : "Подтвердить и пересчитать"}
            </Button>
          ) : null}
        </div>
      </footer>
      {resolve.error || confirm.error || rollback.error ? (
        <p className="form-error">
          {(resolve.error ?? confirm.error ?? rollback.error)?.message}
        </p>
      ) : null}
    </div>
  );
}
