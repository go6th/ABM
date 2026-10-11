"use client";
// A research run in progress, drawn as slices from the run's own progress record, with elapsed
// time and Cancel. Polls; a long run is never held open as one request.
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { m } from "@/lib/messages";

interface Progress { stages_done: number; stages_total: number; finished: boolean; status: string; label: string }

function elapsed(since: string): string {
  const s = Math.max(0, Math.floor((Date.now() - new Date(since).getTime()) / 1000));
  const min = Math.floor(s / 60);
  return min > 0 ? `${min} min ${s % 60} s` : `${s} s`;
}

export function RunStatus({ runId, startedAt }: { runId: string; startedAt: string }) {
  const router = useRouter();
  const [p, setP] = useState<Progress | null>(null);
  const [, tick] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  useEffect(() => {
    let alive = true;
    const load = () => api<Progress>("GET", `/v1/runs/${encodeURIComponent(runId)}/progress`)
      .then((v) => { if (!alive) return; setP(v); if (v.finished) router.refresh(); })
      .catch(() => undefined);
    void load();
    const poll = setInterval(() => void load(), 4000);
    const clock = setInterval(() => tick((n) => n + 1), 1000);
    return () => { alive = false; clearInterval(poll); clearInterval(clock); };
  }, [runId, router]);
  const total = Math.max(p?.stages_total ?? 0, 1);
  const done = Math.min(p?.stages_done ?? 0, total);
  const cancel = async () => {
    setCancelling(true);
    try { await api("POST", `/v1/runs/${encodeURIComponent(runId)}/cancel`); router.refresh(); } catch { setCancelling(false); }
  };
  return (
    <div className="ws-status" role="status" aria-live="polite">
      <div className="line">
        <div className="grow">
          <p className="ws-label">{m.workspace.runTitle}</p>
          <p className="ws-small ws-muted">{p ? m.workspace.runStep(done, total) : m.state.loading}, {elapsed(startedAt)}</p>
        </div>
        <a className="ws-link ws-small" href={`/runs/${encodeURIComponent(runId)}`}>{m.workspace.openRun}</a>
        <button type="button" className="ws-btn quiet" onClick={() => void cancel()} disabled={cancelling}>{m.workspace.cancel}</button>
      </div>
      <div className="ws-progress" aria-hidden="true">
        {Array.from({ length: total }, (_, i) => (
          <span key={i} data-done={i < done} data-current={i === done && !p?.finished} />
        ))}
      </div>
    </div>
  );
}
