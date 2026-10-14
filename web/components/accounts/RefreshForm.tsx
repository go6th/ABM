"use client";
// Refresh from web as designed: three scopes as radio cards (each with its time and credit
// estimate), the credits left this month, Confirm refresh and Stay local. Nothing leaves the
// workspace until Confirm; Confirm sends the credits the user saw, so a moved estimate is shown
// again instead of being charged.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { RefreshOptions, RefreshStarted } from "@/lib/gen/api.gen";

type Option = RefreshOptions["options"][number];

function minutes(m: number): string {
  if (m >= 240) return "About a working day";
  if (m >= 60) return `About ${Math.round(m / 60)} hours`;
  return `About ${m} min`;
}

function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

export function RefreshForm({ view }: { view: RefreshOptions }) {
  const router = useRouter();
  const [options, setOptions] = useState<Option[]>(view.options);
  const start = options.find((o) => o.scope === "people" && o.affordable) ?? options.find((o) => o.affordable) ?? options[0]!;
  const [scope, setScope] = useState<Option["scope"]>(start.scope);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const requestId = useRef(crypto.randomUUID());
  const group = useRef<HTMLDivElement>(null);
  const picked = options.find((o) => o.scope === scope)!;
  const left = view.credits.left;
  const short = picked.credits > left;
  const back = `/accounts/${encodeURIComponent(view.domain)}`;

  const move = (delta: number) => {
    const i = options.findIndex((o) => o.scope === scope);
    const next = options[(i + delta + options.length) % options.length]!;
    setScope(next.scope);
    group.current?.querySelector<HTMLButtonElement>(`[data-scope="${next.scope}"]`)?.focus();
  };

  const confirm = async () => {
    if (busy || short) return;
    setBusy(true);
    setMessage(null);
    try {
      await api<RefreshStarted>("POST", `/v1/accounts/${encodeURIComponent(view.domain)}/refresh`, {
        request_id: requestId.current, scope, credits: picked.credits,
      });
      router.push(back);
      router.refresh();
    } catch (cause) {
      requestId.current = crypto.randomUUID();
      if (cause instanceof ApiError && cause.type === "estimate_changed" && typeof cause.context?.credits === "number") {
        const credits = cause.context.credits as number;
        setOptions((all) => all.map((o) => (o.scope === scope ? { ...o, credits, affordable: credits <= left } : o)));
        setMessage(cause.detail);
      } else if (cause instanceof ApiError && cause.status === 409) {
        setMessage(cause.detail);
      } else if (cause instanceof ApiError && cause.status === 403) {
        setMessage("Web refreshes are turned off for your workspace.");
      } else {
        setMessage("The refresh could not be started. Try again in a moment.");
      }
      setBusy(false);
    }
  };

  return (
    <>
      <div
        ref={group}
        className="opts"
        role="radiogroup"
        aria-label="Refresh scope"
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); move(1); }
          if (e.key === "ArrowUp" || e.key === "ArrowLeft") { e.preventDefault(); move(-1); }
        }}
      >
        {options.map((o) => {
          const on = o.scope === scope;
          return (
            <button
              key={o.scope}
              type="button"
              className="opt"
              role="radio"
              aria-checked={on}
              tabIndex={on ? 0 : -1}
              data-scope={o.scope}
              onClick={() => setScope(o.scope)}
            >
              <span className="in">
                <span className="radio-dot" aria-hidden="true" />
                <span className="grow">
                  <span className="opt-name">{o.name}</span>
                  <span className="opt-desc mut">{o.description}</span>
                </span>
                <span className="opt-cost">
                  <span className="strong">{minutes(o.minutes)}</span>
                  <span className="mut">~{fmt(o.credits)} credits</span>
                </span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="card pad-sm kv">
        <span className="mut">Research credits left this month</span>
        <span>{fmt(left)} of {fmt(view.credits.pool)}</span>
      </div>

      {short ? (
        <div className="notice warn" role="status">
          <p>This refresh needs about {fmt(picked.credits)} credits and {fmt(left)} are left this month. Choose a smaller refresh, or wait until the pool resets on the 1st.</p>
        </div>
      ) : null}
      {message ? <div className="notice error" role="alert"><p>{message}</p></div> : null}

      <div className="actions">
        <button type="button" className="btn primary wide" onClick={() => void confirm()} disabled={busy || short} aria-describedby="refresh-note">
          {busy ? "Starting refresh" : "Confirm refresh"}
        </button>
        <Link className="btn" href={back} prefetch={false}>Stay local</Link>
        <span className="mut small" id="refresh-note">You will be notified when it finishes.</span>
      </div>
    </>
  );
}
