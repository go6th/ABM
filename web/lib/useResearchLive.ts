"use client";
// The Research screen's data, live from the server (owner request 2026-10-21: data on screen within
// a second of being stored; nothing kept in the browser). One server-sent event stream carries the
// whole screen: the server reads it from the database every second and sends it only when it
// changed. The stream ends on purpose (its duration cap, or nothing running) and the browser
// reconnects on its own at the server's ``retry``. Only when the stream cannot be opened at all
// does this read the snapshot (one read at a time, every two seconds, slowing to 30 s while it
// keeps failing), and it tries the stream again with a backoff. A signed-out session ends all of
// it, and a hidden tab holds no stream and reads nothing: when it comes back the stream opens
// again and sends the current snapshot. Every event is checked against the generated contract
// before it is shown.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { ResearchLive } from "@/lib/gen/api.gen";
import { useVisible } from "@/lib/useVisible";

export type LiveState = "live" | "connecting" | "polling";

const POLL_MS = 2000;
const RETRY_MIN_MS = 5000;
const RETRY_MAX_MS = 30000;
// a planned reconnect (the server's retry is at most five seconds) that has not opened by now
// is a real outage: say so instead of "Live"
const STALE_MS = 8000;

// the selected research, as a query parameter ("" when none)
function sel(selected: string | null): string {
  return selected ? encodeURIComponent(selected) : "";
}

/** The parsed snapshot, or null when the event does not match the contract (it is ignored). */
export function parseSnapshot(data: string): ResearchLive | null {
  try {
    const parsed = ResearchLive.safeParse(JSON.parse(data));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export function useResearchLive(initial: ResearchLive | null, selected: string | null) {
  const [data, setData] = useState<ResearchLive | null>(initial);
  const [state, setState] = useState<LiveState>("connecting");
  const [epoch, setEpoch] = useState(0);
  const visible = useVisible();
  // a newer request's answer always wins over an older one still in flight
  const seq = useRef(0);
  const apply = useCallback((next: ResearchLive) => {
    seq.current += 1;
    setData(next);
  }, []);

  /** Open the stream again now (after Start or Stop, so the change shows at once). */
  const restart = useCallback(() => setEpoch((e) => e + 1), []);

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    // a hidden tab keeps no stream open: it opens again, with the current snapshot, when shown
    if (!visible) return;
    const controller = new AbortController();
    let closed = false;
    let halted = false; // signed out: nothing more is asked
    let source: EventSource | null = null;
    let poll: ReturnType<typeof setTimeout> | null = null;
    let polling = false;
    let inFlight = false;
    let pollFails = 0;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoff = RETRY_MIN_MS;
    let stale: ReturnType<typeof setTimeout> | null = null;
    const fresh = () => { if (stale) { clearTimeout(stale); stale = null; } };

    // one snapshot read; a failure slows the next read down, a 401 ends everything
    const read = async () => {
      if (closed || halted || inFlight) return;
      inFlight = true;
      const mine = seq.current;
      try {
        const next = await api<ResearchLive>("GET", `/v1/research/live?selected=${sel(selected)}`, undefined, {}, controller.signal);
        pollFails = 0;
        if (!closed && seq.current === mine) apply(next);
      } catch (cause) {
        if (closed) return;
        if (cause instanceof ApiError && cause.status === 401) {
          halted = true;
          stopPolling();
          if (retry) { clearTimeout(retry); retry = null; }
          source?.close();
          source = null;
          return;
        }
        pollFails += 1;
      } finally {
        inFlight = false;
      }
      if (polling && !closed && !halted) poll = setTimeout(() => void read(), Math.min(POLL_MS * 2 ** Math.max(pollFails - 1, 0), RETRY_MAX_MS));
    };
    const stopPolling = () => { polling = false; if (poll) { clearTimeout(poll); poll = null; } };
    const open = () => {
      if (closed || halted) return;
      source = new EventSource(`/v1/research/live/stream?selected=${sel(selected)}`);
      source.onopen = () => { backoff = RETRY_MIN_MS; fresh(); stopPolling(); setState("live"); };
      source.addEventListener("snapshot", (event) => {
        const next = parseSnapshot((event as MessageEvent<string>).data);
        if (next && !closed) { apply(next); setState("live"); }
      });
      source.onerror = () => {
        if (closed || !source) return;
        // CONNECTING: the stream ended on purpose and the browser reconnects at the server's
        // retry. CLOSED: it could not be opened (signed out, refused, server down)
        if (source.readyState === EventSource.CONNECTING) {
          if (!stale) stale = setTimeout(() => { stale = null; if (!closed) setState("connecting"); }, STALE_MS);
          return;
        }
        source.close();
        source = null;
        setState("polling");
        if (!polling) { polling = true; pollFails = 0; void read(); }
        retry = setTimeout(open, backoff);
        backoff = Math.min(backoff * 2, RETRY_MAX_MS);
      };
    };
    open();
    return () => {
      closed = true;
      controller.abort();
      source?.close();
      stopPolling();
      fresh();
      if (retry) clearTimeout(retry);
    };
  }, [selected, epoch, apply, visible]);

  return { data, state, restart };
}
