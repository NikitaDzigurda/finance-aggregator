import clsx from "clsx";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  CircleOff,
  Info,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { humanStatus } from "../lib/format";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {description ? <p className="page-description">{description}</p> : null}
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Button({
  children,
  variant = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
}) {
  return (
    <button className={clsx("button", `button--${variant}`)} {...props}>
      {children}
    </button>
  );
}

export function ButtonLink({
  to,
  children,
  variant = "primary",
}: {
  to: string;
  children: ReactNode;
  variant?: "primary" | "secondary" | "ghost";
}) {
  return (
    <Link className={clsx("button", `button--${variant}`)} to={to}>
      {children}
    </Link>
  );
}

export function QualityBadge({ state }: { state: string }) {
  const Icon =
    state === "complete" || state === "ready" || state === "completed"
      ? CheckCircle2
      : state === "unavailable" || state === "error" || state === "failed"
        ? CircleOff
        : state === "partial" ||
            state === "warning" ||
            state === "awaiting_review"
          ? AlertTriangle
          : Info;
  return (
    <span className={clsx("status", `status--${state}`)}>
      <Icon size={14} aria-hidden="true" /> {humanStatus(state)}
    </span>
  );
}

export function StatePanel({
  title,
  children,
  kind = "empty",
  action,
}: {
  title: string;
  children: ReactNode;
  kind?: "empty" | "error" | "loading";
  action?: ReactNode;
}) {
  return (
    <section className={clsx("state-panel", `state-panel--${kind}`)}>
      <div className="state-panel__mark" aria-hidden="true" />
      <h2>{title}</h2>
      <div>{children}</div>
      {action ? <div className="state-panel__action">{action}</div> : null}
    </section>
  );
}

export function QueryState({ error }: { error: Error | null }) {
  return error ? (
    <StatePanel title="Не удалось получить данные" kind="error">
      <p>{error.message}</p>
    </StatePanel>
  ) : (
    <StatePanel title="Загружаем данные" kind="loading">
      <p>Запрашиваем актуальное состояние у серверной части.</p>
    </StatePanel>
  );
}

const importSteps = [
  "Источник и счёт",
  "Файлы",
  "Обработка",
  "Предпросмотр",
  "Проверка",
  "Подтверждение",
  "Результат",
];

export function ImportStepper({ active }: { active: number }) {
  return (
    <ol className="import-stepper" aria-label="Этапы импорта">
      {importSteps.map((label, index) => {
        const step = index + 1;
        const complete = step < active;
        return (
          <li
            key={label}
            className={clsx(
              step === active && "is-active",
              complete && "is-complete",
            )}
            aria-current={step === active ? "step" : undefined}
          >
            <span>{complete ? <Check size={14} /> : step}</span>
            <strong>{label}</strong>
          </li>
        );
      })}
    </ol>
  );
}
