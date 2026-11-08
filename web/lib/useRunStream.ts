"use client";
// How an open research run is followed: the run record is read again every POLL_RUN_MS, and its
// progress arrives by event stream (the server ends each stream at its duration cap and the
// browser reconnects on its own at the server's retry) with polling every POLL_PROGRESS_MS
// while no stream is open. A stream that cannot be opened falls back to polling and is tried
// again after STREAM_RETRY_MS. Nothing runs once the run has ended (active = false).
//
// Reading is polite (2026-11-07): the next read waits for the last one to finish (no pile of
// requests behind a slow server), a failed read backs off to POLL_MAX_MS, a run that is gone (404)
// or a session that ended (401) stops it for good, and a hidden tab reads nothing: the stream is
// closed, and when the tab comes back the record is read at once and the stream opens again.
import { useEffect, useRef } from "react";
import { api, ApiError } from "@/lib/api";
import type { RunProgress } from "@/lib/gen/api.gen";
import { useVisible } from "@/lib/useVisible";

export const POLL_RUN_MS = 5000;
export const POLL_PROGRESS_MS = 5000;
export const STREAM_RETRY_MS = 10000;
export const POLL_MAX_MS = 30000;

/** A run that is gone or a session that ended does not come back by asking again. */
export function isGone(cause: unknown): boolean {
  return cause instanceof ApiError && (cause.status === 401 || cause.status === 404);
}

/** The wait after ``fails`` failed reads in a row: the base, doubling each time, at most POLL_MAX_MS. */
export function backoff(base: number, fails: number): number {
  return Math.min(base * 2 ** Math.max(fails - 1, 0), POLL_MAX_MS);
}

/** ``reload`` reads the run record (it may reject with the API's error, which decides whether to
 * keep asking); the signal ends with the effect. */
export function useRunStream(
  runId: string | null,
  active: boolean,
  onProgress: (progress: RunProgress) => void,
  reload: (signal: AbortSignal) => Promise<unknown> | void,
): void {
  const visible = useVisible();
  // set while the tab is hidden: the first read after it comes back is at once
  const wasHidden = useRef(false);
  useEffect(() => {
    if (!runId || !active) return;
    if (!visible) {
      wasHidden.current = true;
      return;
    }
    const immediate = wasHidden.current;
    wasHidden.current = false;
    const controller = new AbortController();
    const { signal } = controller;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let recordTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    let reading = false;
    let progressFails = 0;
    let recordFails = 0;
    const halt = () => {
      closed = true;
      controller.abort();
      source?.close();
      if (timer) clearTimeout(timer);
      if (retry) clearTimeout(retry);
      if (recordTimer) clearTimeout(recordTimer);
    };

    // the run record: the next read is chained after the last one answered
    const pollRecord = async () => {
      recordTimer = null;
      if (closed) return;
      let wait = POLL_RUN_MS;
      try {
        await reload(signal);
        recordFails = 0;
      } catch (cause) {
        if (closed) return;
        if (isGone(cause)) { halt(); return; }
        recordFails += 1;
        wait = backoff(POLL_RUN_MS, recordFails);
      }
      if (!closed) recordTimer = setTimeout(() => void pollRecord(), wait);
    };

    const read = async () => {
      if (closed || reading) return;
      reading = true;
      timer = null;
      let wait = POLL_PROGRESS_MS;
      try {
        onProgress(await api<RunProgress>("GET", `/v1/runs/${encodeURIComponent(runId)}/progress`, undefined, {}, signal));
        progressFails = 0;
      } catch (cause) {
        reading = false;
        if (closed) return;
        if (isGone(cause)) { halt(); return; }
        progressFails += 1;
        wait = backoff(POLL_PROGRESS_MS, progressFails);
      }
      reading = false;
      // polling only while no stream is open
      if (!closed && !source) timer = setTimeout(() => void read(), wait);
    };
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

    recordTimer = setTimeout(() => void pollRecord(), immediate ? 0 : POLL_RUN_MS);
    if (typeof EventSource !== "undefined") open();
    else void read();
    return halt;
  }, [runId, active, visible, onProgress, reload]);
}
