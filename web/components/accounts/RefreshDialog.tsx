"use client";
// Refresh, opened over the account (redesign 2026-11-07): the account header's main button. It
// reads the scopes, their estimates and the researches left when it opens, then shows the same
// form as the Refresh page, with its two-step confirm; nothing is sent until Proceed. Escape, the
// backdrop, Close and Not now close it. The Refresh page itself still works on its own address.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { RefreshOptions } from "@/lib/gen/api.gen";
import { RefreshForm } from "./RefreshForm";

export function RefreshDialog({ domain }: { domain: string }) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<RefreshOptions | null>(null);
  const [failed, setFailed] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const page = `/accounts/${encodeURIComponent(domain)}/refresh`;

  const close = useCallback(() => {
    setOpen(false);
    setTimeout(() => opener.current?.focus(), 0);
  }, []);

  useEffect(() => {
    if (!open) return;
    let live = true;
    api<RefreshOptions>("GET", `/v1/accounts/${encodeURIComponent(domain)}/refresh`)
      .then((v) => { if (live) setView(v); })
      .catch(() => { if (live) setFailed(true); });
    panel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close(); return; }
      if (event.key !== "Tab" || !panel.current) return;
      const focusable = Array.from(panel.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled])"));
      if (!focusable.length) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { live = false; document.removeEventListener("keydown", onKey); };
  }, [open, domain, close]);

  return (
    <>
      <button ref={opener} type="button" className="btn primary" aria-haspopup="dialog" aria-expanded={open} onClick={() => { setView(null); setFailed(false); setOpen(true); }}>Refresh</button>
      {open ? (
        <div className="modal-backdrop" onClick={close}>
          <div ref={panel} className="modal-panel stack-14" role="dialog" aria-modal="true" aria-labelledby="refresh-dialog-title" tabIndex={-1} onClick={(e) => e.stopPropagation()}>
            <button type="button" className="modal-close" aria-label="Close" onClick={close}>×</button>
            <h2 id="refresh-dialog-title">Refresh {view?.name ?? "this account"} from the web</h2>
            {failed ? (
              <p className="sm-text">Refresh is unavailable right now. <Link href={page} prefetch={false}>Open the Refresh page</Link> or try again in a moment.</p>
            ) : !view ? (
              <p className="mut sm-text" role="status">Reading the options…</p>
            ) : !view.allowed ? (
              <p className="sm-text">Your administrator has turned off web refreshes for your workspace. Answers keep coming from what your workspace already holds.</p>
            ) : view.open_job ? (
              <p className="sm-text">A refresh of {view.name} is already {view.open_job.status === "waiting_credit_limit" ? "waiting to start" : "running"}. You will be notified when it finishes.</p>
            ) : (
              <>
                <p className="intro">A refresh looks only for what is new. Nothing leaves your workspace until you confirm.</p>
                <RefreshForm view={view} initialTopic="" onClose={close} />
              </>
            )}
          </div>
        </div>
      ) : null}
    </>
  );
}
