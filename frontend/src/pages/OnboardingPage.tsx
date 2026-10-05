import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Database, Landmark, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, unwrap, type Schema } from "../api/client";
import { useWorkspace } from "../app/workspace-context";
import { Button } from "../components/ui";

export function OnboardingPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { portfolios } = useWorkspace();
  const isAdditionalPortfolio = portfolios.length > 0;
  const [name, setName] = useState("Мой портфель");
  const [currency, setCurrency] = useState<Schema<"ReportingCurrency">>("RUB");
  const [accountName, setAccountName] = useState("Основной брокер");
  const [accountType, setAccountType] = useState<"broker" | "cex">("broker");
  const mutation = useMutation({
    mutationFn: async () => {
      const portfolio = unwrap(
        await api.POST("/api/v1/portfolios", {
          body: { name, base_currency: currency },
        }),
      );
      await api.POST("/api/v1/portfolios/{portfolio_id}/accounts", {
        params: { path: { portfolio_id: portfolio.id } },
        body: { name: accountName, account_type: accountType },
      });
      return portfolio;
    },
    onSuccess: async (portfolio) => {
      await queryClient.invalidateQueries({ queryKey: ["portfolios"] });
      navigate(`/p/${portfolio.id}/imports/new`);
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    mutation.mutate();
  };
  return (
    <div className="onboarding">
      <section className="onboarding__story">
        <div className="brand brand--large">
          <div className="brand__mark" aria-hidden="true">
            <span />
          </div>
          <div>
            <strong>Финансовый</strong>
            <span>агрегатор</span>
          </div>
        </div>
        <p className="eyebrow">
          {isAdditionalPortfolio ? "Новый портфель" : "Первый запуск"}
        </p>
        <h1>
          {isAdditionalPortfolio
            ? "Создайте отдельное пространство для другого набора счетов."
            : "Соберите инвестиционные данные в одном спокойном рабочем пространстве."}
        </h1>
        <div className="promise-list">
          <div>
            <Landmark />
            <span>
              <strong>Брокеры и криптобиржи</strong>
              <small>
                Единый журнал операций без банковских счетов и торговли.
              </small>
            </span>
          </div>
          <div>
            <ShieldCheck />
            <span>
              <strong>Проверка до записи</strong>
              <small>Каждая строка импорта видна до подтверждения.</small>
            </span>
          </div>
          <div>
            <Database />
            <span>
              <strong>Локальное хранение</strong>
              <small>
                Интерфейс работает только через открытый программный интерфейс.
              </small>
            </span>
          </div>
        </div>
      </section>
      <form className="onboarding__form" onSubmit={submit}>
        <p className="step-counter">
          {isAdditionalPortfolio ? "Новый портфель" : "01 / 02"}
        </p>
        <h2>Создайте рабочий контекст</h2>
        <p>Портфель задаёт валюту отчёта, счёт — источник будущего импорта.</p>
        <label>
          Название портфеля
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </label>
        <fieldset>
          <legend>Базовая валюта</legend>
          <div className="segmented">
            {(["RUB", "USD"] as const).map((item) => (
              <button
                key={item}
                type="button"
                aria-pressed={currency === item}
                onClick={() => setCurrency(item)}
              >
                {item === "RUB" ? "Рубли" : "Доллары США"}
              </button>
            ))}
          </div>
        </fieldset>
        <label>
          Название счёта
          <input
            value={accountName}
            onChange={(e) => setAccountName(e.target.value)}
            required
          />
        </label>
        <label>
          Тип счёта
          <select
            value={accountType}
            onChange={(e) => setAccountType(e.target.value as "broker" | "cex")}
          >
            <option value="broker">Брокерский</option>
            <option value="cex">Криптобиржа</option>
          </select>
        </label>
        {mutation.error ? (
          <p className="form-error">{mutation.error.message}</p>
        ) : null}
        <Button disabled={mutation.isPending}>
          {mutation.isPending ? (
            "Создаём…"
          ) : (
            <>
              Продолжить к импорту <ArrowRight size={17} />
            </>
          )}
        </Button>
      </form>
    </div>
  );
}
