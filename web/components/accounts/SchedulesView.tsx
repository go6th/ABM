"use client";
// Schedules as designed: one row per account with its next refresh and the segmented control
// 3 days | Weekly | 2 weeks | Monthly | Off. A change saves at once and the monthly estimate
// below updates from the server. Options faster than the client allows are disabled, with the
// reason read out to screen readers and shown under the list.
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Schedules } from "@/lib/gen/api.gen";

type Cadence = Schedules["rows"][number]["cadence"];

function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

export function SchedulesView({ initial, focus }: { initial: Schedules; focus: string | null }) {
  const [data, setData] = useState<Schedules>(initial);
  const [saving, setSaving] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const anyDisabled = data.rows.some((r) => r.options.some((o) => !o.allowed));

  const save = async (accountId: string, cadence: Cadence) => {
    const before = data;
    setMessage(null);
    setSaving(accountId);
    setData((d) => ({ ...d, rows: d.rows.map((r) => (r.account_id === accountId ? { ...r, cadence } : r)) }));
    try {
      setData(await api<Schedules>("PUT", `/v1/schedules/${encodeURIComponent(accountId)}`, { cadence }));
    } catch (cause) {
      setData(before);
      setMessage(cause instanceof ApiError && cause.status === 422 ? cause.detail : "The schedule could not be saved. Try again.");
    } finally {
      setSaving(null);
    }
  };

  const resets = new Date(`${data.resets_on}T00:00:00Z`).getUTCDate() === 1 ? "resets on the 1st" : `resets on ${data.resets_on}`;
  return (
    <>
      {message ? <div className="notice error" role="alert"><p>{message}</p></div> : null}
      {data.rows.length ? (
        <div className="card clip">
          {data.rows.map((r) => (
            <div key={r.account_id} id={r.domain} className={`sched-row${focus === r.domain ? " focus" : ""}`}>
              <div className="sched-name">
                <div className="strong">{r.name}</div>
                <div className="mut meta">{r.next_label}</div>
              </div>
              <div className="seg" role="group" aria-label={`Refresh schedule for ${r.name}`} aria-busy={saving === r.account_id}>
                {r.options.map((o) => (
                  <button
                    key={o.cadence}
                    type="button"
                    aria-pressed={r.cadence === o.cadence}
                    disabled={!o.allowed || saving === r.account_id}
                    aria-describedby={o.allowed ? undefined : "sched-limit"}
                    onClick={() => { if (r.cadence !== o.cadence) void save(r.account_id, o.cadence); }}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="card state"><h2>No accounts to schedule yet</h2><p>Add an account and its refresh schedule appears here.</p></div>
      )}
      {anyDisabled ? <p className="fine" id="sched-limit">Your administrator allows scheduled refreshes {data.fastest_label} at most.</p> : null}
      <div className="card sched-foot" aria-live="polite">
        <div><b>Estimated use:</b> about {fmt(data.estimate_month)} credits a month at these settings</div>
        <div className="mut small">Pool: {fmt(data.pool)} credits a month · {resets}</div>
      </div>
    </>
  );
}
