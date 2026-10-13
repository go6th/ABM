"use client";
// File analysis: the Overview of an account built from files. Facts marked Internal with file and
// place, a Not in your files list, and a quiet line "From your files, private to your workspace".
// Buttons: Ask about these files, Also check public sources, Download internal summary, Delete files.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { m } from "@/lib/messages";
import { cellText } from "@/lib/render";
import type { InternalView } from "@/lib/gen/api.gen";
import { GradeMark } from "./GradeMark";
import { Loading, Notice } from "@/ui/primitives";

const CATEGORY: Record<string, string> = {
  platforms: "Platforms and partners",
  incumbent_partners: "Platforms and partners",
  programs: "Programmes and initiatives",
  budget_cycles: "Budget cycles and dates",
  objections: "Objections and next steps",
  decisions: "Decisions and stages",
  next_steps: "Objections and next steps",
  people: "People and roles mentioned",
};

export function FileAnalysis({ runId, domain }: { runId: string; domain: string }) {
  const router = useRouter();
  const view = useApi<InternalView>(`/v1/runs/${encodeURIComponent(runId)}/internal`);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  if (view.error) return <Notice kind="error">{m.state.errorTitle} {m.state.errorBody}</Notice>;
  if (!view.data) return <Loading />;
  const data = view.data;
  const groups = new Map<string, InternalView["findings"]>();
  for (const f of data.findings) {
    const label = CATEGORY[f.category] ?? "Other";
    groups.set(label, [...(groups.get(label) ?? []), f]);
  }
  const docs = data.documents.map((d) => ({ id: String(d.document_id ?? ""), name: cellText(String(d.name ?? "")), passages: Number(d.passages ?? 0) }));
  const deleteFiles = async () => {
    if (!window.confirm(m.account.deleteConfirm)) return;
    setBusy(true);
    try {
      for (const d of docs) if (d.id) await api("DELETE", `/v1/documents/${encodeURIComponent(d.id)}`);
      setMessage(m.account.deleted);
      router.refresh();
    } catch {
      setMessage(m.state.errorTitle);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="stack" aria-label={m.account.fromFiles}>
      <p className="small muted">{m.account.fromFiles}</p>
      {[...groups.entries()].map(([label, items]) => (
        <div key={label} className="section">
          <h3>{label}</h3>
          <ul className="facts">
            {items.map((f, i) => (
              <li key={`${label}-${i}`}>
                <GradeMark grade={f.grade === "confirmed" ? "confirmed" : "internal"} />
                <div>
                  <p>{f.value}</p>
                  <p className="cite">{f.citation}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {groups.size === 0 ? <p className="muted">The uploaded files state nothing in the researched areas.</p> : null}
      <div className="section">
        <h3>{m.account.notInFiles}</h3>
        <p className="small muted">{m.account.notInFilesBody}</p>
      </div>
      {docs.length ? (
        <div className="section">
          <h3>{m.research.filesLabel}</h3>
          <ul className="rows">{docs.map((d, i) => <li key={d.id || i}><span>{d.name}</span><span className="end">{d.passages} passages</span></li>)}</ul>
        </div>
      ) : null}
      {message ? <p className="small" role="status">{message}</p> : null}
      <div className="actions">
        <Link className="btn primary" href={`/?account=${encodeURIComponent(domain)}`}>{m.account.askFiles}</Link>
        <Link className="btn" href={`/research/new?mode=file&domain=${encodeURIComponent(domain)}&enrich=1`}>{m.account.checkPublic}</Link>
        {data.file_available ? <a className="btn" href={`/v1/runs/${encodeURIComponent(runId)}/internal/file`}>{m.account.downloadInternal}</a> : null}
        <button type="button" className="btn danger" disabled={busy || !docs.length} onClick={() => void deleteFiles()}>{m.account.deleteFiles}</button>
      </div>
    </section>
  );
}
