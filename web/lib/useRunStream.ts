"use client";
// How an open research run is followed: the run record is read again every POLL_RUN_MS, and its
// progress arrives by event stream (the server ends each stream at its duration cap and the
// browser reconnects on its own at the server's retry) with polling every POLL_PROGRESS_MS
// while no stream is open. A stream that cannot be opened falls back to polling and is tried
// again after STREAM_RETRY_MS. Nothing runs once the run has ended (active = false). Extracted
// from components/ws/RunSteps.tsx unchanged.
import { useEffect } from "react";
import { api } from "@/lib/api";
import type { RunProgress } from "@/lib/gen/api.gen";

export const POLL_RUN_MS = 5000;
export const POLL_PROGRESS_MS = 5000;
export const STREAM_RETRY_MS = 10000;

export function useRunStream(
  runId: string | null,
  active: boolean,
  onProgress: (progress: RunProgress) => void,
  reload: () => void,
): void {
  useEffect(() => {
    if (!runId || !active) return;
    const poll = setInterval(reload, POLL_RUN_MS);
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    const read = async () => {
      if (closed) return;
      try {
        onProgress(await api<RunProgress>("GET", `/v1/runs/${encodeURIComponent(runId)}/progress`));
      } catch { /* the next poll tries again */ }
      // polling only while no stream is open
      if (!closed && !source) timer = setTimeout(() => void read(), POLL_PROGRESS_MS);
    };
    let retry: ReturnType<typeof setTimeout> | null = null;
    const open = () => {
      if (closed) return;
      source = new EventSource(`/v1/runs/${encodeURIComponent(runId)}/events`);
      source.addEventListener("progress", (event) => {
        try { onProgress(JSON.parse((event as MessageEvent<string>).data) as RunProgress); } catch { /* ignore */ }
      });
      source.onerror = () => {
        if (closed || !source || source.readyState === EventSource.CONNECTING) return;
        // it could not be opened: poll, and try the stream again a little later
        source.close();
        source = null;
        void read();
        retry = setTimeout(open, STREAM_RETRY_MS);
      };
    };
    if (typeof EventSource !== "undefined") open();
    else void read();
    return () => { closed = true; clearInterval(poll); source?.close(); if (timer) clearTimeout(timer); if (retry) clearTimeout(retry); };
  }, [runId, active, onProgress, reload]);
}
