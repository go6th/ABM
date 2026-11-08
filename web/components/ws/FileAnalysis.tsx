"use client";
// Analysis of your file (screen 10). The findings from the files, grouped as designed, each
// marked Internal with its file and place; what the files do not cover; Ask about these files,
// Also check public sources, Download the summary of your files; the files used, Add another file and
// Delete these files (confirmed first). While the analysis runs the screen says so and checks the
// analysis itself (one small read, every 5 seconds, slowing to 30) instead of drawing the whole
// page again, and draws the page once when it changes; it stops when the analysis is done, waits
// for an approval or has ended, and reads nothing while the tab is hidden.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { backoff, isGone } from "@/lib/useRunStream";
import { useVisible } from "@/lib/useVisible";
import { cellText } from "@/lib/render";
import type { FileAnalysisRun, InternalView } from "@/lib/gen/api.gen";
import { StateCard } from "@/ui/design";
import { announce } from "@/ui/Toaster";

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
function summary(groups: Map<string, number>, modelUnread = false): string {
  if (modelUnread) {
    return "The text of your files could not be read this time, so this analysis is incomplete. Spreadsheet rows were still read. Analyze the files again later, or ask your 6th contact.";
  }
  if (!groups.size) return "Your files do not state anything in the areas this analysis looks at.";
  // every group, the designed ones first: a file whose findings are all "Other" still gets a sentence
  const named = [...ORDER.filter((g) => groups.has(g)), ...[...groups.keys()].filter((g) => !ORDER.includes(g))];
  const parts = named.map((g) => `${g === "Other" ? "other topics" : g.toLowerCase()} (${groups.get(g)})`);
  const listed = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
  return `Your files speak to ${listed}. Each statement below links to the file and place it came from.`;
}

const FIRST_CHECK_MS = 5000;
const SLOWEST_CHECK_MS = 30000;
// an analysis in one of these states will not finish by itself, so there is nothing to wait for
const STOPPED = new Set(["BUDGET_EXCEEDED", "CANCELED", "FAILED"]);

/** While the analysis is still running, read it every 5 s (slowing to 30 s while nothing changes)
 *  and draw the page once when its state changes. */
function useAnalysisWatch(domain: string, run: FileAnalysisRun, settled: boolean): void {
  const router = useRouter();
  const visible = useVisible();
  const key = `${run.done}:${run.status}`;
  const last = useRef(key);
  useEffect(() => {
    last.current = key;
  }, [key]);
  useEffect(() => {
    if (settled || !visible) return;
    const controller = new AbortController();
    let closed = false;
    let wait = FIRST_CHECK_MS;
    let fails = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      try {
        const next = await api<FileAnalysisRun>("GET", `/v1/accounts/${encodeURIComponent(domain)}/file-analysis?run=${encodeURIComponent(run.run_id)}`, undefined, {}, controller.signal);
        if (closed) return;
        fails = 0;
        const nextKey = `${next.done}:${next.status}`;
        if (nextKey !== last.current) {
          last.current = nextKey;
          wait = FIRST_CHECK_MS;
          router.refresh();
        } else {
          wait = Math.min(wait * 2, SLOWEST_CHECK_MS);
        }
      } catch (cause) {
        if (closed || isGone(cause)) return;
        fails += 1;
        wait = Math.max(wait, backoff(FIRST_CHECK_MS, fails));
      }
      if (!closed) timer = setTimeout(() => void tick(), wait);
    };
    timer = setTimeout(() => void tick(), wait);
    return () => { closed = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [domain, run.run_id, settled, visible, router]);
}

export function FileAnalysis({ domain, run }: { domain: string; run: FileAnalysisRun }) {
  const router = useRouter();
  const view = useApi<InternalView>(run.done ? `/v1/runs/${encodeURIComponent(run.run_id)}/internal` : null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const status = run.status.toUpperCase();
  useAnalysisWatch(domain, run, run.done || status === "AWAITING_APPROVAL" || STOPPED.has(status));

  const head = (
    <div className="row-12 wrap">
      <div className="grow-240">
        <Link className="back" href={`/accounts/${encodeURIComponent(domain)}/files`} prefetch={false}>‹ Files</Link>
        <h2 className="mt-4">Analysis of your files</h2>
        <div className="mut sm-text mt-2">
          Analysis of {run.files} file{run.files === 1 ? "" : "s"} · {when(run.finished_at ?? run.created_at)} · {run.public_run_id ? "public sources checked" : "no web used"}
        </div>
      </div>
      <span className="pill internal big">From your files, private to your workspace</span>
    </div>
  );

  if (!run.done) {
    const runPage = <Link className="btn" href={`/runs/${encodeURIComponent(run.run_id)}`} prefetch={false}>Open the run</Link>;
    return (
      <div className="stack-22">
        {head}
        {status === "AWAITING_APPROVAL" ? (
          <StateCard title="Waiting for your approval" body="The analysis paused and needs a decision before it continues. Nothing leaves your workspace." action={runPage} />
        ) : STOPPED.has(status) ? (
          <StateCard title="The analysis stopped before it finished" body="What it found so far is kept. Open the run to see where it stopped." action={runPage} />
        ) : (
          <StateCard title="Analyzing your files" body="This page updates by itself when the analysis is ready. Nothing leaves your workspace." />
        )}
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
      announce("Files deleted");
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
          <p className="lead-text">{summary(counts, run.model_unread ?? false)}</p>
        </section>
        {sections.length ? (
          <section className="card clip">
            {sections.map((label, i) => (
              <div key={label}>
                <div className={i ? "fa-head line-top" : "fa-head"}><span className="lbl">{label}</span></div>
                {grouped.get(label)!.map((f, j) => (
                  <div className="fa-fact" key={`${label}-${j}`}>
                    <span className="pill internal g">Internal</span>
                    <span className="fa-body">
                      <span>{cellText(f.value)}</span>
                      <span className="fa-cite">{cellText(f.citation)}</span>
                    </span>
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
            <a className="btn" href={`/v1/runs/${encodeURIComponent(run.run_id)}/internal/file`}>Download the summary of your files</a>
          ) : null}
        </div>
      </div>
      <aside className="card fa-rail stack-14" aria-label="Files used">
        <div className="lbl">Files used</div>
        {docs.length ? docs.map((d, i) => (
          <div key={d.id || i} className="body-text">
            {d.name}
            <br />
            <span className="mut xs-text">{d.passages.toLocaleString()} section{d.passages === 1 ? "" : "s"} read</span>
          </div>
        )) : <p className="mut sm-text">The files were deleted.</p>}
        <Link className="link-strong" href="/accounts/import">Add another file</Link>
        <div className="line-top" />
        <div className="lbl">Privacy</div>
        <p className="mut sm-text">Kept in your workspace only. Never included in the research files you share with a prospect. Delete any file and its findings are removed.</p>
        <button type="button" className="link-danger" disabled={busy || !docs.length} aria-busy={busy || undefined} onClick={() => void deleteFiles()}>Delete these files</button>
      </aside>
    </div>
  );
}
