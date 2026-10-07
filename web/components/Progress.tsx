"use client";
// Run progress: stage counts only ("3 of 7"), never an ETA, a provider, a credit count or a
// cost. Server-sent events when the browser supports them; polling with an ETag otherwise.
import { useEffect, useState } from "react";

interface ProgressBody { label: string; text: string; stages_done: number; stages_total: number; current_stage: string | null; finished: boolean }

export function RunProgress({ runId, onFinished }: { runId: string; onFinished?: () => void }) {
  const [p, setP] = useState<ProgressBody | null>(null);
  useEffect(() => {
    let closed = false;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let etag: string | null = null;
    const apply = (body: ProgressBody) => {
      setP(body);
      if (body.finished) { onFinished?.(); closed = true; source?.close(); }
    };
    const poll = async () => {
      if (closed) return;
      const reply = await fetch(`/v1/runs/${runId}/progress`, { headers: etag ? { "If-None-Match": etag } : {}, cache: "no-store" });
      if (reply.status === 200) { etag = reply.headers.get("ETag"); apply((await reply.json()) as ProgressBody); }
      if (!closed) timer = setTimeout(() => void poll(), 5000);
    };
    if (typeof EventSource !== "undefined") {
      source = new EventSource(`/v1/runs/${runId}/events`);
      source.addEventListener("progress", (e) => apply(JSON.parse((e as MessageEvent<string>).data) as ProgressBody));
      source.onerror = () => { source?.close(); source = null; void poll(); };
    } else {
      void poll();
    }
    return () => { closed = true; source?.close(); if (timer) clearTimeout(timer); };
  }, [runId, onFinished]);
  if (!p) return <p className="muted">Checking progress</p>;
  return (
    <div className="row" aria-live="polite">
      <span className="badge">{p.label}</span>
      <div className="progress"><progress max={p.stages_total} value={p.stages_done} aria-label="Stages done" /></div>
      <span>{p.text} stages{p.current_stage ? `, now: ${p.current_stage}` : ""}</span>
    </div>
  );
}
