"use client";
// Analysis of your file (screen 10). The findings from the files, grouped as designed, each
// marked Internal with its file and place; what the files do not cover; Ask about these files,
// Also check public sources, Download internal summary; the files used, Add another file and
// Delete these files (confirmed first). While the analysis runs the screen says so and checks
// again every few seconds.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import type { FileAnalysisRun, InternalView } from "@/lib/gen/api.gen";
import { StateCard } from "@/ui/design";

const SECTION: Record<string, string> = {
  platforms: "Platforms and partners",
  incumbent_partners: "Platforms and partners",
  programs: "Programs and initiatives",
  budget_cycles: "Budget cycles and dates",
  decisions: "Decisions and stages",
  objections: "Objections and next steps",
  next_steps: "Objections and next steps",
  people: "People and roles mentioned",
};
const ORDER = ["Platforms and partners", "Programs and initiatives", "Budget cycles and dates", "Decisions and stages", "Objections and next steps", "People and roles mentioned"];

function when(at: string): string {
  const minutes = Math.round((Date.now() - new Date(at).getTime()) / 60000);
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** A plain summary built from what the files said (no model involved). */
function summary(groups: Map<string, number>): string {
  if (!groups.size) return "Your files do not state anything in the areas this analysis looks at.";
  const parts = ORDER.filter((g) => groups.has(g)).map((g) => `${g.toLowerCase()} (${groups.get(g)})`);
  const listed = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
  return `Your files speak to ${listed}. Each statement below links to the file and place it came from.`;
}

export function FileAnalysis({ domain, run }: { domain: string; run: FileAnalysisRun }) {
  const router = useRouter();
  const view = useApi<InternalView>(run.done ? `/v1/runs/${encodeURIComponent(run.run_id)}/internal` : null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (run.done) return;
    const timer = setInterval(() => router.refresh(), 5000);
    return () => clearInterval(timer);
  }, [run.done, router]);

  const head = (
    <div className="row-12 wrap">
      <div className="grow-240">
        <Link className="back" href="/research/new?tab=file" prefetch={false}>‹ New research</Link>
        <h1 className="big mt-4">{run.account_name}</h1>
        <div className="mut sm-text mt-2">
          Analysis of {run.files} file{run.files === 1 ? "" : "s"} · {when(run.finished_at ?? run.created_at)} · {run.public_run_id ? "public sources checked" : "no web used"}
        </div>
      </div>
      <span className="pill internal big">From your files · private to your workspace</span>
    </div>
  );

  if (!run.done) {
    return (
      <div className="stack-22">
        {head}
        <StateCard title="Analyzing your files" body="This page updates by itself when the analysis is ready. Nothing leaves your workspace." />
      </div>
    );
  }
  if (view.error) {
    return <div className="stack-22">{head}<StateCard title="The analysis could not be loaded" body="Reload the page in a moment." /></div>;
  }
  if (!view.data) return <div className="stack-22">{head}<p className="mut sm-text">Loading the analysis…</p></div>;

  const data = view.data;
  const grouped = new Map<string, InternalView["findings"]>();
  for (const f of data.findings) {
    const label = SECTION[f.category] ?? "Other";
    grouped.set(label, [...(grouped.get(label) ?? []), f]);
  }
  const counts = new Map([...grouped.entries()].map(([k, v]) => [k, v.length]));
  const sections = [...ORDER.filter((g) => grouped.has(g)), ...[...grouped.keys()].filter((g) => !ORDER.includes(g))];
  const docs = data.documents.map((d) => ({ id: String(d.document_id ?? ""), name: cellText(String(d.name ?? "")), passages: Number(d.passages ?? 0) }));

  const deleteFiles = async () => {
    if (!window.confirm("Delete these files? Their findings are removed from your workspace. This cannot be undone.")) return;
    setBusy(true);
    setMessage(null);
    try {
      for (const d of docs) if (d.id) await api("DELETE", `/v1/documents/${encodeURIComponent(d.id)}`);
      setMessage("The files and their findings were deleted.");
      router.refresh();
    } catch {
      setMessage("Some files could not be deleted. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="row-32 wrap align-top">
      <div className="grow-main stack-22">
        {head}
        <section className="card pad-lg stack-10">
          <div className="lbl">What your files say</div>
          <p className="lead-text">{summary(counts)}</p>
        </section>
        {sections.length ? (
          <section className="card clip">
            {sections.map((label, i) => (
              <div key={label}>
                <div className={i ? "fa-head line-top" : "fa-head"}><span className="lbl">{label}</span></div>
                {grouped.get(label)!.map((f, j) => (
                  <div className="fa-fact" key={`${label}-${j}`}>
                    <span className="pill internal g">Internal</span>
                    <span className="grow">{cellText(f.value)}</span>
                    <span className="fa-cite">{cellText(f.citation)}</span>
                  </div>
                ))}
              </div>
            ))}
          </section>
        ) : null}
        <section className="card quiet pad stack-6">
          <div className="lbl">Not in your files</div>
          <p className="body-text">
            Hiring signals, news, technology detections and executive speaking were not searched. Nothing here says they do not exist. Check public sources to fill these in.
          </p>
        </section>
        {message ? <p className="sm-text" role="status">{message}</p> : null}
        <div className="row-12 wrap">
          <Link className="btn" href={`/?account=${encodeURIComponent(domain)}`}>Ask about these files</Link>
          <Link className="btn" href={`/accounts/${encodeURIComponent(domain)}/refresh`}>Also check public sources</Link>
          {data.file_available ? (
            <a className="btn" href={`/v1/runs/${encodeURIComponent(run.run_id)}/internal/file`}>Download internal summary</a>
          ) : null}
        </div>
      </div>
      <aside className="card fa-rail stack-14" aria-label="Files used">
        <div className="lbl">Files used</div>
        {docs.length ? docs.map((d, i) => (
          <div key={d.id || i} className="body-text">
            {d.name}
            <br />
            <span className="mut xs-text">{d.passages} passage{d.passages === 1 ? "" : "s"}</span>
          </div>
        )) : <p className="mut sm-text">The files were deleted.</p>}
        <Link className="link-strong" href="/research/new?tab=file">Add another file</Link>
        <div className="line-top" />
        <div className="lbl">Privacy</div>
        <p className="mut sm-text">Kept in your workspace only. Never included in the research files you share with a prospect. Delete any file and its findings are removed.</p>
        <button type="button" className="link-danger" disabled={busy || !docs.length} onClick={() => void deleteFiles()}>Delete these files</button>
      </aside>
    </div>
  );
}
