import { useId } from "react";
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
} from "react";
import { grades, trackerStatuses, type Accent } from "../tokens";

type ButtonVariant = "default" | "primary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

export function Button({
  children,
  className = "",
  variant = "default",
  size = "md",
  loading = false,
  disabled,
  type = "button",
  ...props
}: ButtonProps) {
  const classes = ["btn", variant === "default" ? "" : variant, size === "md" ? "" : size, className]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      {...props}
      className={classes}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading ? <span className="spinner" aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

export interface IconButtonProps extends ButtonProps {
  "aria-label": string;
}

export function IconButton({ children, className = "", ...props }: IconButtonProps) {
  return (
    <Button {...props} className={["icon", className].filter(Boolean).join(" ")}>
      {children}
    </Button>
  );
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
  error?: string;
}

export function Input({
  id,
  label,
  hint,
  error,
  className = "",
  required,
  ...props
}: InputProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  const describedBy = [props["aria-describedby"], hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className="field">
      <label htmlFor={inputId}>
        {label}
        {required ? <span aria-hidden="true"> *</span> : null}
      </label>
      <input
        {...props}
        id={inputId}
        className={["input", className].filter(Boolean).join(" ")}
        required={required}
        aria-invalid={error ? true : props["aria-invalid"]}
        aria-describedby={describedBy}
      />
      {hint ? <span id={hintId} className="hint">{hint}</span> : null}
      {error ? <span id={errorId} className="field-error">{error}</span> : null}
    </div>
  );
}

export interface CardProps extends HTMLAttributes<HTMLElement> {
  title?: string;
  description?: string;
  children: ReactNode;
}

export function Card({ title, description, children, className = "", ...props }: CardProps) {
  return (
    <section {...props} className={["surface", "card", className].filter(Boolean).join(" ")}>
      {title ? <h2 className="card-title">{title}</h2> : null}
      {description ? <p className="card-description">{description}</p> : null}
      {children}
    </section>
  );
}

export type Grade = keyof typeof grades;

export function GradeBadge({ grade }: { grade: Grade }) {
  const { label, accent } = grades[grade];
  return <span className={`badge tone-${accent}`} aria-label={`Evidence grade: ${label}`}>{label}</span>;
}

export type CoverageStatus = keyof typeof trackerStatuses;

export function StatusChip({ status }: { status: CoverageStatus }) {
  const { label, accent } = trackerStatuses[status];
  return <span className={`badge tone-${accent}`} aria-label={`Coverage status: ${label}`}>{label}</span>;
}

export function Progress({
  value,
  max = 100,
  label,
}: {
  value?: number;
  max?: number;
  label: string;
}) {
  const validValue = typeof value === "number" && Number.isFinite(value) && value >= 0 &&
    Number.isFinite(max) && max > 0 && value <= max;
  return (
    <progress
      className="ui-progress"
      aria-label={label}
      max={Number.isFinite(max) && max > 0 ? max : 100}
      value={validValue ? value : undefined}
    />
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <section className="empty" aria-label={title}>
      {icon ? <span className="empty-icon" aria-hidden="true">{icon}</span> : null}
      <h2 className="card-title">{title}</h2>
      <p>{description}</p>
      {action}
    </section>
  );
}

export function Skeleton({
  variant = "line",
  label = "Loading",
  className = "",
}: {
  variant?: "line" | "title" | "block";
  label?: string;
  className?: string;
}) {
  return <span className={["skeleton", variant, className].filter(Boolean).join(" ")} role="status" aria-label={label} />;
}

export function Notice({
  tone,
  title,
  children,
}: {
  tone: Exclude<Accent, "lavender">;
  title: string;
  children: ReactNode;
}) {
  const role = tone === "rose" ? "alert" : "status";
  return (
    <div className={`banner tone-${tone}`} role={role}>
      <div className="banner-body"><strong>{title}</strong><div>{children}</div></div>
    </div>
  );
}

export interface DataColumn<Row> {
  key: string;
  header: ReactNode;
  render: (row: Row) => ReactNode;
}

export function DataTable<Row>({
  caption,
  columns,
  rows,
  rowKey,
  loading = false,
  error,
  emptyMessage = "No records to show.",
}: {
  caption: string;
  columns: readonly DataColumn<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  loading?: boolean;
  error?: string;
  emptyMessage?: string;
}) {
  const stateMessage = loading ? "Loading records…" : error ?? emptyMessage;
  return (
    <div className="ui-table-scroll" role="region" aria-label={caption} tabIndex={0}>
      <table className="ui-table" aria-busy={loading}>
        <caption className="sr-only">{caption}</caption>
        <thead><tr>{columns.map((column) => <th key={column.key} scope="col">{column.header}</th>)}</tr></thead>
        <tbody>
          {loading || error || rows.length === 0 ? (
            <tr><td className="ui-table-state" colSpan={Math.max(columns.length, 1)} role={error ? "alert" : loading ? "status" : undefined}>{stateMessage}</td></tr>
          ) : rows.map((row) => (
            <tr key={rowKey(row)}>{columns.map((column) => <td key={column.key}>{column.render(row)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
