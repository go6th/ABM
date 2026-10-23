"use client";
// The token card of a research run: the exact total as the headline with the run's state beside
// it, rate or run time, model cost and the share of what a research of this kind used before; a
// bar that fills step by step in each step's colour against marks; and every step with its tokens
// and share. Widths are SVG attributes because the page CSP forbids inline styles.
import { useEffect, useState } from "react";
import type { RunStatusView, TokenUse } from "@/lib/gen/api.gen";

export const TOKENS_PER_MARK = 100_000;
const MARK_STEPS = [100_000, 200_000, 250_000, 500_000, 1_000_000, 2_000_000, 2_500_000, 5_000_000, 10_000_000, 20_000_000, 50_000_000];
const MAX_MARKS = 6;

/** 950 · 12.4K · 2.5M */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2).replace(/\.?0+$/, "")}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
  return String(Math.round(n));
}

function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/** The bar's full width and its marks: 0 to the larger of the total and the expectation, in at
 * most six even steps. */
export function scaleFor(total: number, expected: number | null): { max: number; marks: number[] } {
  const top = Math.max(total, expected ?? 0, TOKENS_PER_MARK);
  const step = MARK_STEPS.find((st) => Math.ceil(top / st) <= MAX_MARKS - 1) ?? MARK_STEPS.at(-1)!;
  const max = Math.ceil(top / step) * step;
  return { max, marks: Array.from({ length: max / step + 1 }, (_, i) => i * step) };
}

export type MeterStep = { id: string; name: string; state: string };
export type MeterState = "running" | "paused" | "finished" | "stopped" | "waiting";

export function meterState(status: string, started: boolean): MeterState {
  if (!started) return "waiting";
  if (status === "COMPLETED" || status === "PARTIAL") return "finished";
  if (status === "CANCELED" || status === "FAILED" || status === "BUDGET_EXCEEDED") return "stopped";
  if (status === "AWAITING_APPROVAL") return "paused";
  return "running";
}

const STEP_IDS = new Set(["setup", "search", "read", "follow_up", "write", "build", "gate", "deliver", "other"]);

function pill(state: MeterState, at: { index: number; total: number } | null) {
  const text = {
    running: at ? `Running · step ${at.index + 1} of ${at.total}` : "Running",
    paused: "Waiting for approval",
    finished: "Finished",
    stopped: "Stopped",
    waiting: "Not started",
  }[state];
  return <span className={`tk-pill tk-pill-${state}`}><i aria-hidden="true" />{text}</span>;
}

export function TokenMeter({ run, steps = [], at = null, status }: {
  /** the run's token figures (the whole run view, or just its token card) */
  run: Pick<RunStatusView, "token_use"> & Partial<Pick<RunStatusView, "tokens_used">>;
  /** the run's steps with their states (``RunProgress.steps`` or the mapped list) */
  steps?: MeterStep[];
  /** the step the run is at, for the "step 4 of 8" pill */
  at?: { index: number; total: number } | null;
  status: string;
}) {
  const use: TokenUse = run.token_use ?? { total: run.tokens_used ?? 0 };
  const total = Math.max(0, Math.floor(use.total));
  const reused = use.reused ?? 0;
  const expected = use.expected_tokens ?? null;
  const state = meterState(status, Boolean(use.started_at) || total > 0 || steps.some((s) => s.state !== "waiting"));
  const live = state === "running" || state === "paused";

  // the clock for a running research's rate, read after render and every 15 seconds
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const every = live ? setInterval(tick, 15_000) : undefined;
    return () => { clearTimeout(first); if (every) clearInterval(every); };
  }, [live]);
  const startMs = use.started_at ? Date.parse(use.started_at) : NaN;
  const endMs = use.finished_at ? Date.parse(use.finished_at) : now ?? NaN;
  const seconds = Number.isFinite(startMs) && Number.isFinite(endMs) ? Math.max(0, (endMs - startMs) / 1000) : null;
  const perMinute = seconds && seconds >= 30 ? (total / seconds) * 60 : null;

  const { max, marks } = scaleFor(total, expected);
  const next = (Math.floor(total / TOKENS_PER_MARK) + 1) * TOKENS_PER_MARK;
  const stateOf = new Map(steps.map((s) => [s.id, s.state]));
  const rows = (use.steps?.length ? use.steps : []).map((s) => ({ ...s, state: stateOf.get(s.id) ?? (s.tokens ? "done" : "waiting") }));
  const filled = rows.filter((s) => s.tokens > 0);
  const segments = filled.map((s, i) => ({ id: s.id, x: filled.slice(0, i).reduce((a, b) => a + b.tokens, 0), w: s.tokens }));
  const counted = filled.reduce((a, b) => a + b.tokens, 0);
  // a total the steps do not account for (a run off the research graph) fills as one segment
  if (counted < total) segments.push({ id: "all", x: counted, w: total - counted });

  const sub = live
    ? total
      ? `Next mark at ${formatTokens(next)} · ${formatTokens(next - total)} to go`
      : "Counting starts with the first model call"
    : state === "waiting"
      ? "Counting starts when research begins"
      : "Input and output tokens the model served";
  const expectedNote = expected === null
    ? "No estimate for this kind of research"
    : use.expected_runs
      ? `Median of the last ${use.expected_runs} research${use.expected_runs === 1 ? "" : "es"} of this kind: ${formatTokens(expected)}`
      : `Rough estimate before any past research: ${formatTokens(expected)}`;

  return (
    <section className="tk-card" role="group" aria-label="Token usage of this research">
      <div className="tk-top">
        <div className="tk-head">
          <div className="tk-label">Token usage {pill(state, at)}</div>
          <div className="tk-big" aria-live="polite">{total.toLocaleString("en-US")}<small>tokens</small></div>
          <div className="tk-sub">{sub}</div>
        </div>
        <dl className="tk-stats">
          <div className="tk-stat">
            <dd>{state === "waiting" ? "—" : live ? (perMinute === null ? "—" : `${formatTokens(perMinute)}/min`) : seconds === null ? "—" : duration(seconds)}</dd>
            <dt>{live || state === "waiting" ? "Average rate" : "Run time"}</dt>
          </div>
          <div className="tk-stat">
            <dd>{state === "waiting" ? "—" : `$${(use.cost_usd ?? 0).toFixed(2)}`}</dd>
            <dt>Model cost</dt>
          </div>
          <div className="tk-stat" title={expectedNote}>
            <dd>{state === "waiting" || expected === null ? "—" : `${Math.round((total / Math.max(1, expected)) * 100)}%`}</dd>
            <dt>of expected</dt>
          </div>
        </dl>
      </div>

      {total === 0 ? (
        <div className="tk-empty">
          <b>No tokens used yet</b>
          {reused
            ? `${formatTokens(reused)} tokens were reused from earlier research so far, at no cost.`
            : live ? "The meter fills step by step as the research calls the model." : "The meter fills step by step once research starts."}
        </div>
      ) : (
        <>
          <div className="tk-bar" aria-hidden="true">
            <svg viewBox={`0 0 ${max} 12`} preserveAspectRatio="none" focusable="false">
              {segments.map((s) => (
                <rect key={s.id} className={`tk-seg tk-c-${STEP_IDS.has(s.id) ? s.id : "all"}`} x={s.x} y={0} width={s.w} height={12} />
              ))}
              {expected !== null && expected <= max ? <rect className="tk-expected" x={Math.max(0, expected - max * 0.002)} y={0} width={max * 0.004} height={12} /> : null}
            </svg>
          </div>
          <div className="tk-marks" aria-hidden="true">
            {marks.map((v) => <span key={v}>{v ? formatTokens(v) : "0"}</span>)}
          </div>
          {reused ? <p className="tk-note">{formatTokens(reused)} more tokens were reused from earlier research, at no cost.</p> : null}
          {rows.length ? (
            <ul className="tk-legend" aria-label="Tokens by step">
              {rows.map((s) => {
                const quiet = !s.tokens;
                return (
                  <li key={s.id} className={`tk-row${quiet ? " tk-wait" : ""}`}>
                    <span className={`tk-dot tk-c-${quiet ? "none" : STEP_IDS.has(s.id) ? s.id : "all"}`} aria-hidden="true" />
                    <span className="tk-n">{s.name}</span>
                    <span className="tk-t">{s.tokens ? formatTokens(s.tokens) : s.state === "done" ? "0" : "—"}</span>
                    <span className="tk-p">{s.tokens ? `${Math.round((s.tokens / total) * 100)}%` : ""}</span>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </>
      )}
    </section>
  );
}
