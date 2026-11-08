"use client";
// Research progress (redesign 2026-11-07): the research's steps in plain words with done, running,
// waiting or where it stopped, the tokens so far, Stop, the checkpoint card when it pauses, and its
// files as they appear. The page updates itself (no Refresh button). Under the title: how to leave
// it while it runs, or how it ended; nothing while the run is missing or not yet loaded. The quality check's codes and the coverage table are not
// shown here: what could not be found is on the account's Coverage table. The page and the panel
// share one copy of the run's polling and event stream.
import Link from "next/link";
import { useParams } from "next/navigation";
import { RunFiles, RunPanel, useRun } from "@/components/ws/RunSteps";
import { m } from "@/lib/messages";
import { cellText } from "@/lib/render";

export default function RunPage() {
  const params = useParams<{ run_id: string }>();
  const runId = params.run_id;
  const live = useRun(runId);
  const { run, status, terminal } = live;
  // a run that is not in the workspace has no files to wait for
  const missing = live.error?.status === 404;
  // the same split as the panel's closing line: these ended before they finished
  const stoppedEarly = status === "FAILED" || status === "CANCELED" || status === "BUDGET_EXCEEDED";
  const sub = run?.input_mode === "file"
    ? m.account.fromFiles
    : !run
      ? null
      : stoppedEarly
        ? "It stopped before it finished. What it found so far is on the account."
        : terminal
          ? "Finished. Everything it found is on the account."
          : "You can leave this page. You are notified when it finishes.";
  const account = run?.domain ? `/accounts/${encodeURIComponent(run.domain)}` : null;
  const title = run?.domain ? `${terminal ? "Research of" : "Researching"} ${cellText(run.domain)}` : m.run.title;
  return (
    <div className="pg">
      <div className="head">
        <div className="ttl">
          <div className="crumb"><Link href="/research" prefetch={false}>Research</Link></div>
          <h1>{title}</h1>
          {sub ? <div className="sub">{sub}</div> : null}
        </div>
        {account ? <Link className="btn" href={account} prefetch={false}>{m.run.openAccount}</Link> : null}
      </div>
      <div className="run-grid">
        <div className="stack-22">
          <RunPanel runId={runId} state={live} />
          {terminal && account ? (
            <p className="mut sm-text">What this research could not find, and why, is on the account&apos;s <Link href={`${account}/coverage`} prefetch={false}>Coverage</Link> table.</p>
          ) : null}
        </div>
        {missing ? null : (
          <aside className="stack-14" aria-label={m.run.files}>
            <h2 className="h-sm">{m.run.files}</h2>
            <RunFiles domain={run?.domain ?? null} runId={runId} status={status} />
            {run?.models_line ? <p className="mut xs-text">{run.models_line}</p> : null}
          </aside>
        )}
      </div>
    </div>
  );
}
