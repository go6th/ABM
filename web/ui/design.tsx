// Small pieces of the Claude Design prototype shared by every screen: the logo mark, the nav
// icons (the design's own inline SVG paths), grade and status pills, the progress bar (an SVG
// rect width, because the CSP forbids inline styles) and the skeleton blocks.
import type { ReactNode } from "react";

export function LogoMark({ size = 28, inverse = false }: { size?: number; inverse?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true">
      <rect width="28" height="28" rx="8" fill={inverse ? "#fff" : "#101828"} />
      <path d="M8 19l6-10 6 10" fill="none" stroke={inverse ? "#101828" : "#fff"} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="14" cy="16.5" r="1.6" fill={inverse ? "#2F4BE0" : "#7C93FF"} />
    </svg>
  );
}

export function Logo({ name = "Account Intelligence", by = "by 6th", inverse = false }: { name?: string; by?: string; inverse?: boolean }) {
  return (
    <span className={inverse ? "logo inverse" : "logo"}>
      <LogoMark inverse={inverse} />
      <span className="name">{name}<span className="by">{by}</span></span>
    </span>
  );
}

const ICON_PATHS: Record<string, ReactNode> = {
  ask: <path d="M4 5h16v11H9l-5 4z" />,
  accounts: (
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </>
  ),
  research: <path d="M12 16V5M7 10l5-5 5 5M5 19h14" />,
  runs: <path d="M3 12h4l3-7 4 14 3-7h4" />,
  support: <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z" />,
  schedules: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4l3 2" />
    </>
  ),
  mail: <path d="M9 7V4M15 7V4M7 7h10v4a5 5 0 0 1-10 0zM12 16v4" />,
};

export type IconName = keyof typeof ICON_PATHS;

export function NavIcon({ name }: { name: string }) {
  return (
    <span className="ni" aria-hidden="true">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        {ICON_PATHS[name]}
      </svg>
    </span>
  );
}

export type PillTone = "ok" | "probable" | "warn" | "run" | "internal" | "neutral" | "bad" | "lead";

export function Pill({ tone = "neutral", small, big, children }: { tone?: PillTone; small?: boolean; big?: boolean; children: ReactNode }) {
  return <span className={["pill", tone, small ? "g" : "", big ? "big" : ""].filter(Boolean).join(" ")}>{children}</span>;
}

const GRADE_TONE: Record<string, PillTone> = {
  confirmed: "ok",
  probable: "probable",
  hypothesis: "warn",
  lead: "lead",
  internal: "internal",
};

const GRADE_LABEL: Record<string, string> = {
  confirmed: "Confirmed",
  probable: "Probable",
  hypothesis: "Hypothesis",
  lead: "Lead",
  internal: "Internal",
};

/** A fact grade as the design draws it: one colour per grade, the word always written. */
export function Grade({ grade }: { grade: string | null | undefined }) {
  const key = (grade ?? "").toLowerCase();
  return <Pill tone={GRADE_TONE[key] ?? "neutral"} small>{GRADE_LABEL[key] ?? (grade || "Ungraded")}</Pill>;
}

export function gradeLabel(grade: string | null | undefined): string {
  const key = (grade ?? "").toLowerCase();
  return GRADE_LABEL[key] ?? (grade || "Ungraded");
}

/** A horizontal progress bar. `value` and `max` are numbers; the fill is clamped to 0..100%. */
export function Bar({ value, max, height = 6, tone, soft, label }: { value: number; max: number; height?: number; tone?: "warn"; soft?: boolean; label?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  const r = height / 2;
  return (
    <svg className={["bar", tone ?? "", soft ? "soft" : ""].filter(Boolean).join(" ")} height={height} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <rect className="track" x="0" y="0" width="100%" height={height} rx={r} />
      {pct > 0 ? <rect className="fill" x="0" y="0" width={`${pct}%`} height={height} rx={r} /> : null}
    </svg>
  );
}

export function Skeleton({ kind = "line", width }: { kind?: "line" | "block" | "row" | "title"; width?: "w40" | "w60" | "w80" }) {
  return <div className={["skel", kind, width ?? ""].filter(Boolean).join(" ")} aria-hidden="true" />;
}

/** The empty, loading and error states every screen uses, in one card. */
export function StateCard({ title, body, action }: { title: string; body?: ReactNode; action?: ReactNode }) {
  return (
    <div className="card state">
      <h2>{title}</h2>
      {body ? <p>{body}</p> : null}
      {action}
    </div>
  );
}
