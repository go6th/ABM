"use client";
// Download everything (owner decision 2026-10-14): every deliverable, every table whole (CSV and
// Excel) and your own files with their findings, built in the background. A large archive comes
// in parts; each link works for an hour and a broken download continues where it stopped.
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { fileSize } from "@/lib/documents";
import type { DownloadJob } from "@/lib/gen/api.gen";

const POLL_MS = 3000;

export function DownloadEverything({ domain }: { domain: string }) {
  const [job, setJob] = useState<DownloadJob | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const requestId = useRef(crypto.randomUUID());

  useEffect(() => {
    if (!job || (job.status !== "queued" && job.status !== "running")) return;
    const timer = setTimeout(() => {
      api<DownloadJob>("GET", `/v1/downloads/${job.job_id}`).then(setJob).catch(() => undefined);
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [job]);

  const start = async () => {
    setMessage(null);
    try {
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
  if (job.status === "queued" || job.status === "running") {
    return <p className="mut sm-text" role="status"><span className="spinner" aria-hidden="true" /> Preparing everything for download. This can take a few minutes.</p>;
  }
  if (job.status !== "ready") {
    return (
      <div className="stack-8" role="status">
        <p className="sm-text">{job.status === "expired" ? "This download has expired." : job.failure ?? "The download could not be prepared."}</p>
        <button type="button" className="btn sm align-start" onClick={() => { requestId.current = crypto.randomUUID(); setJob(null); }}>Try again</button>
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
