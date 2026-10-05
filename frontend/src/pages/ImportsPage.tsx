import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Plus } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import {
  ButtonLink,
  PageHeader,
  QualityBadge,
  QueryState,
  StatePanel,
} from "../components/ui";
import { formatDate, sourceProviderLabel } from "../lib/format";

export function ImportsPage() {
  const { portfolioId = "" } = useParams();
  const query = useQuery({
    queryKey: ["imports", portfolioId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/imports", {
          params: { query: { portfolio_id: portfolioId, limit: 100 } },
        }),
      ),
  });
  if (query.isLoading || !query.data) return <QueryState error={query.error} />;
  return (
    <div className="page imports-page">
      <PageHeader
        eyebrow="Трассируемые источники"
        title="Импорты"
        description="Загрузка, проверка и подтверждение отчётов брокеров и криптобирж."
        actions={
          <ButtonLink to={`/p/${portfolioId}/imports/new`}>
            <Plus size={16} /> Новый импорт
          </ButtonLink>
        }
      />
      {!query.data.items.length ? (
        <StatePanel
          title="История импортов пуста"
          action={
            <ButtonLink to={`/p/${portfolioId}/imports/new`}>
              Загрузить первый отчёт
            </ButtonLink>
          }
        >
          <p>
            Поддерживаемые форматы запрашиваются у серверной части. Исходные
            строки останутся связанными с журналом операций.
          </p>
        </StatePanel>
      ) : (
        <section className="data-section import-history">
          <div className="compact-table">
            <div className="compact-table__head compact-table__head--imports">
              <span>Файл и источник</span>
              <span>Период</span>
              <span>Строки</span>
              <span>Статус</span>
              <span />
            </div>
            {query.data.items.map((batch) => (
              <Link
                className="compact-table__row compact-table__row--imports"
                key={batch.id}
                to={`/p/${portfolioId}/imports/${batch.id}`}
              >
                <span>
                  <strong>{batch.original_filename}</strong>
                  <small>
                    {sourceProviderLabel(batch.source_provider)} ·{" "}
                    {batch.declared_format.toUpperCase()}
                  </small>
                </span>
                <span>
                  {batch.reporting_period_start && batch.reporting_period_end
                    ? `${batch.reporting_period_start} — ${batch.reporting_period_end}`
                    : "Не определён"}
                  <small>{formatDate(batch.created_at)}</small>
                </span>
                <span>
                  <strong>{batch.total_rows}</strong>
                  <small>
                    {batch.warning_rows +
                      batch.error_rows +
                      batch.duplicate_rows}{" "}
                    требуют внимания
                  </small>
                </span>
                <span>
                  <QualityBadge state={batch.status} />
                </span>
                <span>
                  <ArrowRight size={16} />
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
