import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, Search } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { api, unwrap, type Schema } from "../api/client";
import { Button, PageHeader, QueryState, StatePanel } from "../components/ui";
import {
  currencyLabel,
  identifierTypeLabel,
  instrumentTypeLabel,
  userFacingName,
} from "../lib/format";

export function InstrumentsPage() {
  const { portfolioId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const returnTo = searchParams.get("return");
  const query = useQuery({
    queryKey: ["instruments"],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/instruments", {
          params: { query: { limit: 100 } },
        }),
      ),
  });
  const [search, setSearch] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<Schema<"InstrumentType">>("stock");
  const [currency, setCurrency] = useState("USD");
  const [identifierType, setIdentifierType] =
    useState<Schema<"InstrumentIdentifierType">>("ticker");
  const [identifier, setIdentifier] = useState("");
  const [scope, setScope] = useState("");
  const filtered = useMemo(
    () =>
      (query.data?.items ?? []).filter((item) =>
        `${item.name} ${item.currency} ${item.instrument_type} ${item.identifiers.map((value) => value.value).join(" ")}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [query.data, search],
  );
  const create = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/instruments", {
          body: {
            name,
            instrument_type: type,
            currency,
            identifiers: [
              {
                identifier_type: identifierType,
                value: identifier,
                exchange: identifierType === "ticker" ? scope || null : null,
                provider:
                  identifierType === "provider_code" ? scope || null : null,
              },
            ],
          },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["instruments"] });
      if (returnTo?.startsWith(`/p/${portfolioId}/imports/`))
        navigate(returnTo);
      else {
        setName("");
        setIdentifier("");
      }
    },
  });
  if (query.isLoading || !query.data) return <QueryState error={query.error} />;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate();
  };
  return (
    <div className="page instruments-page">
      <PageHeader
        eyebrow="Канонический справочник"
        title="Инструменты"
        description="Справочник используется для явного сопоставления строк импорта; импорт не создаёт инструменты автоматически."
        actions={
          returnTo ? (
            <Link className="back-link" to={returnTo}>
              <ArrowLeft size={16} /> Вернуться к проверке
            </Link>
          ) : undefined
        }
      />
      <div className="instruments-layout">
        <section className="data-section instrument-list">
          <div className="toolbar">
            <label className="search-field">
              <Search size={17} />
              <input
                aria-label="Поиск инструментов"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Название, код, валюта"
              />
            </label>
          </div>
          {!filtered.length ? (
            <StatePanel title="Инструменты не найдены">
              <p>
                {query.data.items.length
                  ? "Измените строку поиска."
                  : "Создайте первый канонический инструмент справа."}
              </p>
            </StatePanel>
          ) : (
            filtered.map((item) => (
              <article key={item.id} className="instrument-row">
                <span>
                  <strong>{userFacingName(item.name)}</strong>
                  <small>
                    {instrumentTypeLabel(item.instrument_type)} ·{" "}
                    {currencyLabel(item.currency)}
                  </small>
                </span>
                <span>
                  {item.identifiers
                    .map(
                      (value) =>
                        `${identifierTypeLabel(value.identifier_type)}: ${value.value}${value.exchange ? ` · ${value.exchange}` : ""}`,
                    )
                    .join("; ")}
                </span>
              </article>
            ))
          )}
        </section>
        <form className="action-panel instrument-create" onSubmit={submit}>
          <p className="eyebrow">Новая запись</p>
          <h2>Создать инструмент</h2>
          <label>
            Название
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </label>
          <div className="field-row">
            <label>
              Тип
              <select
                value={type}
                onChange={(event) =>
                  setType(event.target.value as Schema<"InstrumentType">)
                }
              >
                {[
                  "stock",
                  "bond",
                  "etf",
                  "fund",
                  "option",
                  "crypto_asset",
                  "currency",
                ].map((value) => (
                  <option key={value} value={value}>
                    {instrumentTypeLabel(value)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Валюта
              <input
                value={currency}
                onChange={(event) =>
                  setCurrency(event.target.value.toUpperCase())
                }
                maxLength={3}
                required
              />
            </label>
          </div>
          <label>
            Идентификатор
            <select
              value={identifierType}
              onChange={(event) =>
                setIdentifierType(
                  event.target.value as Schema<"InstrumentIdentifierType">,
                )
              }
            >
              <option value="ticker">Биржевой код</option>
              <option value="isin">Международный код ценной бумаги</option>
              <option value="provider_code">Код поставщика</option>
              <option value="crypto_asset_code">Код криптоактива</option>
            </select>
          </label>
          <label>
            Значение
            <input
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
              required
            />
          </label>
          {identifierType === "ticker" || identifierType === "provider_code" ? (
            <label>
              {identifierType === "ticker" ? "Биржа" : "Поставщик"}
              <input
                value={scope}
                onChange={(event) => setScope(event.target.value)}
                required
              />
            </label>
          ) : null}
          {create.error ? (
            <p className="form-error">{create.error.message}</p>
          ) : null}
          <Button disabled={create.isPending}>
            <Plus size={16} />{" "}
            {create.isPending ? "Создаём…" : "Создать и продолжить"}
          </Button>
        </form>
      </div>
    </div>
  );
}
