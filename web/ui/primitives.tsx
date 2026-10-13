// Primitives shared by every screen. Server-safe: no hooks here.
import Link from "next/link";
import type { ReactNode } from "react";
import { m } from "@/lib/messages";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function PageHead({ title, sub, actions, crumb }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; crumb?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        {crumb ? <p className="crumb">{crumb}</p> : null}
        <h1>{title}</h1>
        {sub ? <p className="sub">{sub}</p> : null}
      </div>
      {actions ? <div className="actions">{actions}</div> : null}
    </header>
  );
}

export function Section({ title, children, id, action }: { title: ReactNode; children: ReactNode; id?: string; action?: ReactNode }) {
  return (
    <section className="section" id={id} aria-labelledby={id ? `${id}-title` : undefined}>
      <div className="row between">
        <h2 id={id ? `${id}-title` : undefined}>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export type StatusTone = "ok" | "warn" | "bad" | "neutral";

/** A colour and always a label. */
export function Status({ tone, children, plain }: { tone: StatusTone; children: ReactNode; plain?: boolean }) {
  return <span className={cx("status", tone, plain && "plain")}>{children}</span>;
}

export function Notice({ kind = "info", children, role }: { kind?: "info" | "error"; children: ReactNode; role?: "alert" | "status" }) {
  return <div className={cx("notice", kind === "error" && "error")} role={role ?? (kind === "error" ? "alert" : undefined)}>{children}</div>;
}

export function EmptyState({ title, body, action }: { title: string; body?: ReactNode; action?: { href: string; label: string } | ReactNode }) {
  const isLink = action && typeof action === "object" && "href" in (action as object);
  return (
    <div className="empty">
      <h3>{title}</h3>
      {body ? <p>{body}</p> : null}
      {action ? (isLink ? <Link className="btn primary" href={(action as { href: string }).href}>{(action as { label: string }).label}</Link> : (action as ReactNode)) : null}
    </div>
  );
}

export function Loading({ label = m.state.loading }: { label?: string }) {
  return <p className="loading" role="status"><span className="spinner" aria-hidden="true" />{label}</p>;
}

export function LoadError({ reference, retryHref }: { reference: string | null; retryHref: string }) {
  return (
    <div className="notice error" role="alert">
      <p>{m.state.errorTitle} {m.state.errorBody}</p>
      {reference ? <p className="small muted">{m.state.reference(reference)}</p> : null}
      <div><a className="btn sm" href={retryHref}>{m.state.retry}</a></div>
    </div>
  );
}

export function Field({ id, label, hint, error, children }: { id: string; label: ReactNode; hint?: ReactNode; error?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint ? <p className="hint" id={`${id}-hint`}>{hint}</p> : null}
      {error ? <p className="field-error" id={`${id}-error`} role="alert">{error}</p> : null}
    </div>
  );
}
