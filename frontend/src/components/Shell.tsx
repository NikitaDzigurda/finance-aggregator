import { useQuery } from "@tanstack/react-query";
import {
  ArrowDownToLine,
  BriefcaseBusiness,
  CalendarDays,
  ChevronLeft,
  Home,
  Menu,
  PieChart,
  Plus,
  ShieldCheck,
  X,
} from "lucide-react";
import { useState } from "react";
import {
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { api, unwrap } from "../api/client";
import { useWorkspace } from "../app/workspace-context";
import { formatDate, userFacingName } from "../lib/format";

const navItems = [
  { path: "overview", label: "Обзор", icon: Home },
  { path: "holdings", label: "Активы", icon: BriefcaseBusiness },
  { path: "imports", label: "Импорт", icon: ArrowDownToLine },
  { path: "allocation", label: "Распределение", icon: PieChart },
  { path: "events", label: "События", icon: CalendarDays },
  { path: "data-quality", label: "Качество данных", icon: ShieldCheck },
];

export function Shell() {
  const { portfolioId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { portfolios, currency, setCurrency } = useWorkspace();
  const [open, setOpen] = useState(false);
  const overview = useQuery({
    queryKey: ["overview", portfolioId, currency],
    enabled: Boolean(portfolioId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/portfolios/{portfolio_id}/analytics/overview", {
          params: {
            path: { portfolio_id: portfolioId },
            query: { reporting_currency: currency },
          },
        }),
      ),
  });
  const snapshotAsOf = overview.data?.snapshot_as_of;
  const hasStaleInputs = overview.data?.diagnostics.some((item) =>
    ["market_price_stale", "fx_rate_stale"].includes(item.code),
  );
  return (
    <div className="app-shell">
      <aside className={open ? "sidebar sidebar--open" : "sidebar"}>
        <div className="brand">
          <div className="brand__mark" aria-hidden="true">
            <span />
          </div>
          <span className="sr-only">Финансовый агрегатор</span>
          <button
            className="icon-button sidebar__close"
            onClick={() => setOpen(false)}
            aria-label="Закрыть меню"
          >
            <X />
          </button>
        </div>
        <nav aria-label="Основная навигация">
          {navItems.map(({ path, label, icon: Icon }) => (
            <NavLink
              key={path}
              to={`/p/${portfolioId}/${path}`}
              onClick={() => setOpen(false)}
            >
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar__footer">
          <ChevronLeft size={16} />
          <span>Свернуть</span>
        </div>
      </aside>
      {open ? (
        <button
          className="scrim"
          aria-label="Закрыть меню"
          onClick={() => setOpen(false)}
        />
      ) : null}
      <div className="workspace">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            onClick={() => setOpen(true)}
            aria-label="Открыть меню"
          >
            <Menu />
          </button>
          <div className="topbar__portfolio-group">
            <label className="topbar__portfolio">
              <span className="sr-only">Текущий портфель</span>
              <select
                value={portfolioId}
                onChange={(event) => {
                  const section = location.pathname.split("/")[3] || "overview";
                  navigate(`/p/${event.target.value}/${section}`);
                }}
              >
                {portfolios.map((item) => (
                  <option key={item.id} value={item.id}>
                    {userFacingName(item.name)}
                  </option>
                ))}
              </select>
            </label>
            <NavLink
              className="topbar__create-portfolio"
              to="/start"
              aria-label="Создать новый портфель"
              title="Создать новый портфель"
            >
              <Plus size={17} aria-hidden="true" />
              <span>Новый портфель</span>
            </NavLink>
          </div>
          <div className="topbar__tools">
            <div className="currency-switch" aria-label="Валюта отчёта">
              {(["RUB", "USD"] as const).map((item) => (
                <button
                  key={item}
                  aria-pressed={currency === item}
                  onClick={() => setCurrency(item)}
                >
                  {item === "RUB" ? "₽" : "$"}
                </button>
              ))}
            </div>
            <div className="topbar__calculation" aria-live="polite">
              {snapshotAsOf ? (
                <span className="topbar__freshness">
                  <span>
                    Рассчитано по сохранённым операциям, ценам и курсам
                  </span>
                  <small>Дата расчёта: {formatDate(snapshotAsOf)}</small>
                </span>
              ) : overview.isLoading ? (
                <span className="topbar__calculation-state">
                  Проверяем состояние расчёта
                </span>
              ) : (
                <span className="topbar__calculation-state topbar__calculation-state--missing">
                  Расчёт ещё не выполнен
                </span>
              )}
              {snapshotAsOf && !overview.data?.snapshot_fresh ? (
                <span className="topbar__calculation-state topbar__calculation-state--stale">
                  Расчёт устарел
                </span>
              ) : snapshotAsOf && hasStaleInputs ? (
                <span className="topbar__calculation-state topbar__calculation-state--stale">
                  Есть устаревшие цены или курсы
                </span>
              ) : null}
            </div>
            <NavLink
              className="topbar__add"
              to={`/p/${portfolioId}/imports/new`}
            >
              Добавить данные
            </NavLink>
          </div>
        </header>
        <main>
          <Outlet />
        </main>
        <footer className="market-data-attribution">
          <a href="https://coinpaprika.com/" target="_blank" rel="noreferrer">
            Powered by Coinparika
          </a>
        </footer>
      </div>
    </div>
  );
}
