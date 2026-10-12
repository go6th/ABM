"use client";
// Run progress: stage counts only ("3 of 7"), never an ETA, a provider, a credit count or a
// cost. Use the progress stream with ETag polling as a resilient fallback.
import { useEffect, useState } from "react";

interface ProgressBody {
  label: string;
  text: string;
  stages_done: number;
  stages_total: number;
  current_stage: string | null;
  finished: boolean;
}

export function RunProgress({ runId, onFinished }: { runId: string; onFinished?: () => void }) {
  const [progress, setProgress] = useState<ProgressBody | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let closed = false;
    let polling = false;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let etag: string | null = null;
    let unchangedPolls = 0;
    const delay = () => unchangedPolls < 2 ? 2000 : unchangedPolls < 6 ? 5000 : 15000;

    const apply = (body: ProgressBody) => {
      setProgress(body);
      setFailure(null);
      if (body.finished && !closed) {
        closed = true;
        source?.close();
        onFinished?.();
      }
    };

    const poll = async () => {
      if (closed || document.visibilityState === "hidden") return;
      if (timer) { clearTimeout(timer); timer = null; }
      try {
        const reply = await fetch(`/v1/runs/${encodeURIComponent(runId)}/progress`, {
          headers: etag ? { "If-None-Match": etag } : {},
          credentials: "same-origin",
          cache: "no-store",
        });
        if (reply.status === 200) {
          etag = reply.headers.get("ETag");
          apply(await reply.json() as ProgressBody);
          unchangedPolls = 0;
        } else if (reply.status === 304) {
          unchangedPolls += 1;
        } else {
          const body = await reply.json().catch(() => null) as { request_id?: unknown } | null;
          const requestId = typeof body?.request_id === "string" ? ` Request ID: ${body.request_id}` : "";
          if (reply.status === 401) {
            setFailure(`Your session has expired. Sign in again to continue.${requestId}`);
            closed = true;
            return;
          }
          setFailure(`Progress is temporarily unavailable.${requestId}`);
          unchangedPolls += 1;
        }
      } catch {
        setFailure("Progress is temporarily unavailable. Retrying automatically.");
        unchangedPolls += 1;
      }
      if (!closed && document.visibilityState === "visible") {
        timer = setTimeout(() => void poll(), delay());
      }
    };

    const openStream = () => {
      if (closed || polling || source || document.visibilityState === "hidden") return;
      source = new EventSource(`/v1/runs/${encodeURIComponent(runId)}/events`);
      source.addEventListener("progress", (event) => {
        try { apply(JSON.parse((event as MessageEvent<string>).data) as ProgressBody); }
        catch { setFailure("The progress update could not be read. Reconnecting automatically."); }
      });
      source.addEventListener("reconnect", () => {
        source?.close();
        source = null;
        openStream();
      });
      source.onerror = () => {
        source?.close();
        source = null;
        polling = true;
        void poll();
      };
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible" && !closed) {
        if (polling) void poll();
        else openStream();
      } else {
        source?.close();
        source = null;
        if (timer) { clearTimeout(timer); timer = null; }
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    if (typeof EventSource === "undefined") {
      polling = true;
      void poll();
    } else {
      openStream();
    }
    return () => {
      closed = true;
      source?.close();
      document.removeEventListener("visibilitychange", onVisibility);
      if (timer) clearTimeout(timer);
    };
  }, [runId, onFinished]);

  if (!progress) return <p className="ink-2" role="status">{failure ?? "Checking progress"}</p>;
  const total = Math.max(progress.stages_total, 1);
  const done = Math.min(progress.stages_done, total);
  return (
    <div className="stack" aria-live="polite">
      <div className="row">
        <span className="badge peri dot">{progress.label}</span>
        <span>{progress.text} stages{progress.current_stage ? `, now: ${progress.current_stage}` : ""}</span>
      </div>
      <progress className="sr-only" max={total} value={done} aria-label="Stages done" />
      <div className="slices" aria-hidden="true">
        {Array.from({ length: total }, (_, i) => <span key={i} data-done={i < done} data-current={i === done && !progress.finished} />)}
      </div>
      {failure ? <span role="status" className="t-footnote ink-2">{failure}</span> : null}
    </div>
  );
}
