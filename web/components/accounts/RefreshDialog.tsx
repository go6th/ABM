"use client";
// Refresh, opened over the account (redesign 2026-11-07): the account header's main button. It
// reads the scopes, their estimates and the researches left when it opens, then shows the same
// form as the Refresh page with its two-press confirm. It is the shared Modal: Escape, the
// backdrop and Close close it, except while a start is being sent, so its outcome is always seen.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { RefreshOptions } from "@/lib/gen/api.gen";
import { Modal } from "@/ui/Modal";
import { RefreshForm } from "./RefreshForm";

export function RefreshDialog({ domain }: { domain: string }) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<RefreshOptions | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const close = useCallback(() => { setOpen(false); setBusy(false); }, []);

  useEffect(() => {
    if (!open) return;
    let live = true;
    api<RefreshOptions>("GET", `/v1/accounts/${encodeURIComponent(domain)}/refresh`)
      .then((v) => { if (live) setView(v); })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [open, domain, attempt]);

  const retry = () => { setFailed(false); setAttempt((n) => n + 1); };

  return (
    <>
      <button type="button" className="btn primary" aria-haspopup="dialog" aria-expanded={open} onClick={() => { setView(null); setFailed(false); setOpen(true); }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.9-3M4 13a8 8 0 0 0 14.9 3" /><path d="M4 4v4h4M20 20v-4h-4" /></svg>
        Refresh
      </button>
      {open ? (
        <Modal title={`Refresh ${view?.name ?? "this account"} from the web`} onClose={close} busy={busy} wide>
          {failed ? (
            <div className="stack-8">
              <p className="sm-text">The refresh options could not be read.</p>
              <button type="button" className="btn sm align-start" onClick={retry}>Try again</button>
            </div>
          ) : !view ? (
            <div className="stack-12" role="status" aria-label="Reading the options">
              <div className="skel row" /><div className="skel row" /><div className="skel row" />
            </div>
          ) : !view.allowed ? (
            <p className="sm-text">Your administrator has turned off web refreshes for your workspace. Answers keep coming from what your workspace already holds.</p>
          ) : view.open_job ? (
            <p className="sm-text">A refresh of {view.name} is already {view.open_job.status === "waiting_credit_limit" ? "waiting to start" : "running"}. You will be notified when it finishes.</p>
          ) : (
            <>
              <p className="mut sm-text">A refresh looks only for what is new since the last research.</p>
              <RefreshForm view={view} initialTopic="" onClose={close} onBusy={setBusy} />
            </>
          )}
        </Modal>
      ) : null}
    </>
  );
}
