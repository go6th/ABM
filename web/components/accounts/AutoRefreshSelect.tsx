"use client";
// Auto refresh for one account, edited in its row on the Accounts list (redesign 2026-11-07): the
// same choices and limits as Settings, Auto refresh, saved at once. Options faster than the
// workspace allows are disabled.
import { useState } from "react";
import { api, errorText } from "@/lib/api";
import type { ScheduleRow, Schedules } from "@/lib/gen/api.gen";
import { announce } from "@/ui/Toaster";

const LABEL: Record<string, string> = { "3d": "Every 3 days", weekly: "Weekly", "2w": "Every 2 weeks", monthly: "Monthly", off: "Off" };

export function AutoRefreshSelect({ row }: { row: ScheduleRow }) {
  const [cadence, setCadence] = useState(row.cadence);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (next: string) => {
    if (busy) return;
    const before = cadence;
    setCadence(next);
    setBusy(true);
    setError(null);
    try {
      const saved = await api<Schedules>("PUT", `/v1/schedules/${encodeURIComponent(row.account_id)}`, { cadence: next });
      setCadence(saved.rows.find((r) => r.account_id === row.account_id)?.cadence ?? next);
      announce("Auto refresh saved");
    } catch (cause) {
      setCadence(before);
      setError(errorText(cause, "Not saved. Try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="ar-cell">
      <select className="select sm" aria-label={`Auto refresh for ${row.name}`} value={cadence} disabled={busy} aria-busy={busy || undefined} onChange={(e) => void save(e.target.value)}>
        {row.options.map((o) => (
          <option key={o.cadence} value={o.cadence} disabled={!o.allowed}>{o.label || LABEL[o.cadence] || o.cadence}</option>
        ))}
      </select>
      {error ? <span className="bad-text xs-text" role="alert">{error}</span> : null}
    </span>
  );
}
