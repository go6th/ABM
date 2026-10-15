"use client";
// "View what was read" for one uploaded file (audit 2026-10-15: users could not see what a file
// contributed): its passages in order with where each came from (page, slide, row), fifty at a
// time. Loaded only when opened.
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { DocumentPassage, DocumentPassages } from "@/lib/gen/api.gen";
import { cellText } from "@/lib/render";

export function WhatWasRead({ documentId, name }: { documentId: string; name: string }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<DocumentPassage[]>([]);
  const [next, setNext] = useState<number | null>(-1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (after: number) => {
    setBusy(true);
    setError(null);
    try {
      const page = await api<DocumentPassages>("GET", `/v1/documents/${encodeURIComponent(documentId)}/passages?after=${after}&limit=50`);
      setItems((prev) => [...prev, ...page.passages]);
      setNext(page.next_after ?? null);
    } catch (e) {
      setError(e instanceof ApiError && e.detail ? e.detail : "What was read could not be loaded. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const toggle = () => {
    const opening = !open;
    setOpen(opening);
    if (opening && !items.length && next === -1) void load(-1);
  };

  return (
    <div className="stack-8">
      <button type="button" className="link-btn small align-start" aria-expanded={open} onClick={toggle}>
        {open ? "Hide what was read" : "View what was read"}
      </button>
      {open ? (
        <section className="card quiet pad-sm stack-8" aria-label={`What was read from ${name}`}>
          {error ? <p className="sm-text bad-text" role="alert">{error}</p> : null}
          {!items.length && busy ? <p className="small muted" role="status">Loading what was read</p> : null}
          {!items.length && !busy && !error ? <p className="small muted">Nothing was read from this file.</p> : null}
          <ol className="rows uploads" aria-label={`Passages of ${name}`}>
            {items.map((p) => (
              <li key={p.seq}>
                <div>
                  <span className="sub">{p.where ?? `Passage ${p.seq + 1}`}{p.heading ? ` · ${cellText(p.heading)}` : ""}</span>
                  <p className="small">{cellText(p.content)}</p>
                </div>
              </li>
            ))}
          </ol>
          {next !== null && next !== -1 ? (
            <button type="button" className="btn sm align-start" disabled={busy} onClick={() => void load(next)}>
              {busy ? "Loading" : "Load more"}
            </button>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
