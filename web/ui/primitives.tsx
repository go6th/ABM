// Primitives shared by every screen (docs/DESIGN_SYSTEM.md). Server-safe: no hooks here, so
// Server Components can render them. Interactive pieces live in their own client modules.
import Link from "next/link";
import type { ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info, TriangleAlert, Inbox } from "lucide-react";
import { m } from "@/lib/messages";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export type Tone = "peri" | "mint" | "peach" | "lilac" | "rose" | "butter";

export function PageHead({ title, sub, actions, eyebrow }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {sub ? <p className="sub">{sub}</p> : null}
      </div>
      {actions ? <div className="actions">{actions}</div> : null}
    </header>
  );
}

export function Section({ title, sub, action, children, id, labelledBy }: { title: ReactNode; sub?: ReactNode; action?: ReactNode; children: ReactNode; id?: string; labelledBy?: string }) {
  const headingId = labelledBy ?? (id ? `${id}-title` : undefined);
  return (
    <section className="section" id={id} aria-labelledby={headingId}>
      <div className="section-head">
        <div>
          <h2 id={headingId}>{title}</h2>
          {sub ? <p className="sub">{sub}</p> : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Badge({ tone, dot, children }: { tone?: Tone; dot?: boolean; children: ReactNode }) {
  return <span className={cx("badge", tone, dot && "dot")}>{children}</span>;
}

const BANNER_ICON = { info: Info, success: CheckCircle2, warn: TriangleAlert, error: AlertCircle, web: Info, neutral: Info } as const;

export function Banner({ kind = "info", title, children, action, role }: { kind?: keyof typeof BANNER_ICON; title?: ReactNode; children?: ReactNode; action?: ReactNode; role?: "alert" | "status" }) {
  const Icon = BANNER_ICON[kind];
  return (
    <div className={cx("banner", kind)} role={role ?? (kind === "error" ? "alert" : undefined)}>
      <Icon size={18} strokeWidth={1.8} aria-hidden="true" />
      <div className="grow">
        {title ? <p>{title}</p> : null}
        {children ? <div className="t-subhead">{children}</div> : null}
      </div>
      {action}
    </div>
  );
}

export function EmptyState({ title, body, action, icon }: { title: string; body: ReactNode; action?: { href: string; label: string } | ReactNode; icon?: ReactNode }) {
  const isLink = action && typeof action === "object" && "href" in (action as object) && "label" in (action as object);
  return (
    <div className="empty">
      <span className="empty-mark" aria-hidden="true">{icon ?? <Inbox size={20} strokeWidth={1.8} />}</span>
      <h3>{title}</h3>
      <p>{body}</p>
      {action ? (
        <div className="actions">
          {isLink ? <Link className="btn primary" href={(action as { href: string }).href}>{(action as { label: string }).label}</Link> : (action as ReactNode)}
        </div>
      ) : null}
    </div>
  );
}

export function LoadError({ reference, retryHref }: { reference: string | null; retryHref: string }) {
  return (
    <div className="banner error" role="alert">
      <AlertCircle size={18} strokeWidth={1.8} aria-hidden="true" />
      <div className="grow">
        <p>{m.state.errorTitle}</p>
        <p className="t-subhead">{m.state.errorBody}</p>
        {reference ? <p className="t-footnote">{m.state.reference(reference)}</p> : null}
      </div>
      <a className="btn sm" href={retryHref}>{m.state.retry}</a>
    </div>
  );
}

/** A horizontal meter drawn in SVG, so its width needs no inline style. */
export function Meter({ value, max, tone, label }: { value: number; max: number; tone?: "warn" | "full"; label?: string }) {
  const fraction = max > 0 ? Math.min(Math.max(value / max, 0), 1) : 0;
  return (
    <svg className="meter" viewBox="0 0 100 6" preserveAspectRatio="none" data-tone={tone} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true} focusable="false">
      <rect className="track" x="0" y="0" width="100" height="6" rx="3" />
      <rect className="fill" x="0" y="0" width={Math.max(fraction * 100, value > 0 ? 3 : 0)} height="6" rx="3" />
    </svg>
  );
}

export function Tile({ tone = "peri", label, value, note, icon }: { tone?: Tone; label: ReactNode; value: ReactNode; note?: ReactNode; icon?: ReactNode }) {
  return (
    <div className={cx("tile", tone)}>
      {icon}
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {note ? <span className="note">{note}</span> : null}
    </div>
  );
}

export function PageSkeleton({ width = "content" }: { width?: "content" | "reading" | "wide" }) {
  return (
    <div className={cx("stack", width)} aria-busy="true">
      <span className="sr-only" role="status">{m.state.loading}</span>
      <span className="skeleton title" />
      <span className="skeleton line" />
      <span className="skeleton line short" />
      <span className="skeleton block" />
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

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

export function Monogram({ name }: { name: string }) {
  const letters = name.replace(/^www\./, "").replace(/[^a-z0-9]/gi, "").slice(0, 2) || "?";
  return <span className="monogram" aria-hidden="true">{letters}</span>;
}
