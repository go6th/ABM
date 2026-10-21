"use client";
// Download everything (owner decision 2026-10-14): every deliverable, every table whole (CSV and
// Excel) and your own files with their findings, built in the background. A large archive comes
// in parts; each link works for an hour and a broken download continues where it stopped.
// While it is prepared the status is read every few seconds; a failed read is retried with a
// longer wait (it never stops the polling), the time taken so far is shown, and after a while
// "Check again" and "Start over" appear. The server ends a build nobody is working on any more as
// failed, so the page then offers Try again.
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { fileSize } from "@/lib/documents";
import type { DownloadJob } from "@/lib/gen/api.gen";

const POLL_MS = 3000;
const MAX_WAIT_MS = 30000; // the longest wait between reads after failed ones
const SLOW_MS = 5 * 60 * 1000; // after this, offer Check again and Start over

function waited(ms: number): string {
  const min = Math.floor(ms / 60000);
  return min < 1 ? "less than a minute" : min === 1 ? "1 minute" : `${min} minutes`;
}

export function DownloadEverything({ domain }: { domain: string }) {
  const [job, setJob] = useState<DownloadJob | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [misses, setMisses] = useState(0);
  const [poll, setPoll] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [startedAt, setStartedAt] = useState(0);
  const requestId = useRef(crypto.randomUUID());
  const preparing = job?.status === "queued" || job?.status === "running";

  useEffect(() => {
    if (!job || !preparing) return;
    let alive = true;
    const wait = Math.min(MAX_WAIT_MS, POLL_MS * 2 ** misses);
    const timer = setTimeout(() => {
      api<DownloadJob>("GET", `/v1/downloads/${encodeURIComponent(job.job_id)}`)
        .then((next) => { if (alive) { setMisses(0); setJob(next); setPoll((n) => n + 1); } })
        .catch((cause: unknown) => {
          if (!alive) return;
          if (cause instanceof ApiError && cause.status === 404) { setJob({ ...job, status: "failed", failure: "This download was not found. Try again." }); return; }
          setMisses((n) => n + 1);
          setPoll((n) => n + 1);
        })
        .finally(() => { if (alive) setNow(Date.now()); });
    }, wait);
    return () => { alive = false; clearTimeout(timer); };
    // poll counts every read, so an unchanged status still schedules the next one
  }, [job, preparing, misses, poll]);

  // show "Get new links" once the links pass their hour (the page may stay open that long)
  useEffect(() => {
    if (job?.status !== "ready" || !job.links_expire_at) return;
    const left = Date.parse(job.links_expire_at) - Date.now();
    if (left <= 0) return;
    const timer = setTimeout(() => setNow(Date.now()), left + 500);
    return () => clearTimeout(timer);
  }, [job]);

  const start = async () => {
    setMessage(null);
    try {
      const at = Date.now();
      setStartedAt(at);
      setNow(at);
      setMisses(0);
      setJob(await api<DownloadJob>("POST", `/v1/accounts/${encodeURIComponent(domain)}/download-everything`, { request_id: requestId.current }));
    } catch (cause) {
      requestId.current = crypto.randomUUID();
      setMessage(cause instanceof ApiError ? cause.detail : "The download could not be started. Try again.");
    }
  };

  if (!job) {
    return (
      <div className="stack-8">
        <button type="button" className="btn" onClick={() => void start()}>Download everything</button>
        {message ? <p className="sm-text bad-text" role="alert">{message}</p> : null}
      </div>
    );
  }
  if (preparing) {
    const took = now - startedAt;
    const startOver = () => { requestId.current = crypto.randomUUID(); setJob(null); setMisses(0); };
    return (
      <div className="stack-8" role="status" aria-live="polite">
        <p className="mut sm-text">
          <span className="spinner" aria-hidden="true" /> {job.status === "queued" ? "Waiting to start preparing" : "Preparing everything for download"}. This can take a few minutes. Time so far: {waited(took)}.
        </p>
        {misses ? <p className="sm-text">The status could not be read just now. Trying again.</p> : null}
        {took >= SLOW_MS ? (
          <div className="actions">
            <button type="button" className="btn sm" onClick={() => { setMisses(0); setPoll((n) => n + 1); }}>Check again</button>
            <button type="button" className="btn sm" onClick={startOver}>Start over</button>
          </div>
        ) : null}
      </div>
    );
  }
  if (job.status !== "ready") {
    return (
      <div className="stack-8" role="status">
        <p className="sm-text">{job.status === "expired" ? "This download has expired." : job.failure ?? "The download could not be prepared."}</p>
        <button type="button" className="btn sm align-start" onClick={() => { requestId.current = crypto.randomUUID(); setJob(null); setMisses(0); }}>Try again</button>
      </div>
    );
  }
  // a link works for an hour: past that, reading the status again gives fresh links
  const stale = job.links_expire_at ? Date.parse(job.links_expire_at) <= now : false;
  const renew = () => {
    api<DownloadJob>("GET", `/v1/downloads/${encodeURIComponent(job.job_id)}`).then(setJob).catch(() => setMessage("New links could not be made. Try again."));
  };
  if (stale) {
    return (
      <div className="stack-8" role="status">
        <p className="sm-text">The download links have expired. The archive is still kept.</p>
        {message ? <p className="sm-text bad-text" role="alert">{message}</p> : null}
        <button type="button" className="btn sm align-start" onClick={renew}>Get new links</button>
      </div>
    );
  }
  return (
    <div className="stack-8" role="status">
      <p className="sm-text">Ready: {job.files} files, {fileSize(job.total_bytes)}{job.parts.length > 1 ? `, in ${job.parts.length} parts` : ""}. Links work for an hour.</p>
      <ul className="uploads" aria-label="Download parts">
        {job.parts.map((p) => (
          <li key={p.n}>
            <span className="sub">{p.name} · {fileSize(p.bytes)}</span>
            <a className="btn sm" href={p.url} download>{job.parts.length > 1 ? `Download part ${p.n}` : "Download"}</a>
          </li>
        ))}
      </ul>
    </div>
  );
}
