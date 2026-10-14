"use client";
// Add an account as designed: three numbered ways. 1 Research a new company (one of the user's
// researches, confirmed first); 2 Ready-made research shared with your team (6th Library
// accounts published to this client, as checkbox cards; adding them uses no research); 3
// Analyze your own file (New research, file tab). Accounts are not capped (owner decision
// 2026-10-14: researches and chats are).
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { LibraryAdded, LibraryAvailable, ResearchEstimate } from "@/lib/gen/api.gen";
import { ResearchCompany } from "./ResearchCompany";

export function AddAccount({ estimate, library }: { estimate: ResearchEstimate | null; library: LibraryAvailable | null }) {
  const router = useRouter();
  const offered = (library?.accounts ?? []).filter((a) => !a.in_workspace);
  const already = (library?.accounts ?? []).filter((a) => a.in_workspace);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const toggle = (id: string) => setSelected((all) => (all.includes(id) ? all.filter((x) => x !== id) : [...all, id]));

  const add = async () => {
    if (busy || !selected.length) return;
    setBusy(true);
    setMessage(null);
    try {
      await api<LibraryAdded>("POST", "/v1/accounts/from-library", { library_account_ids: selected });
      router.push("/accounts");
      router.refresh();
    } catch (cause) {
      setMessage(cause instanceof ApiError && cause.status === 409 ? cause.detail : "The accounts could not be added. Try again in a moment.");
      setBusy(false);
    }
  };

  return (
    <>
      <section className="card pad stack-14" aria-labelledby="way-1">
        <div className="lbl" id="way-1">1 · Research a new company</div>
        <ResearchCompany estimate={estimate} idPrefix="add" />
      </section>

      <section className="card pad stack-14" aria-labelledby="way-2">
        <div className="lbl" id="way-2">2 · Ready-made research shared with your team</div>
        <p className="mut sm-text lead-tight">Research already completed. Add it and you can ask questions right away, with no wait.</p>
        {library === null ? (
          <p className="mut sm-text">Ready-made research is unavailable right now. Reload the page in a moment.</p>
        ) : offered.length === 0 && already.length === 0 ? (
          <p className="mut sm-text">No ready-made research has been shared with your team yet.</p>
        ) : (
          <>
            {offered.length ? (
              <div className="lib-grid" role="group" aria-label="Ready-made accounts">
                {offered.map((a) => {
                  const on = selected.includes(a.id);
                  return (
                    <button key={a.id} type="button" className="opt" aria-pressed={on} onClick={() => toggle(a.id)}>
                      <span className="in lib">
                        <span className="box lg" aria-hidden="true">✓</span>
                        <span className="strong">{a.name}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : null}
            {already.length ? (
              <p className="mut sm-text">Already in your workspace: {already.map((a) => a.name).join(", ")}.</p>
            ) : null}
            {message ? <p className="sm-text" role="alert">{message}</p> : null}
            {offered.length ? (
              <div className="row-14 wrap">
                <button type="button" className="btn" disabled={!selected.length || busy} onClick={() => void add()}>
                  {busy ? "Adding…" : `Add ${selected.length} to workspace`}
                </button>
                <span className="mut sm-text">Ready to ask about right away. Uses no research.</span>
              </div>
            ) : null}
          </>
        )}
      </section>

      <section className="card pad row-16 wrap" aria-labelledby="way-3">
        <div className="grow-280">
          <div className="lbl" id="way-3">3 · Analyze your own file</div>
          <p className="mut sm-text mt-6">Upload account notes, a plan or an export and get an analysis from the file alone.</p>
        </div>
        <Link className="btn" href="/research/new?tab=file">Upload a file</Link>
      </section>
    </>
  );
}
