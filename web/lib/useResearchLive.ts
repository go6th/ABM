"use client";
// The Research screen's data, live from the server (owner request 2026-10-21: data on screen within
// a second of being stored; nothing kept in the browser). One server-sent event stream carries the
// whole screen: the server reads it from the database every second and sends it only when it
// changed. The stream ends on purpose (its duration cap, or nothing running) and the browser
// reconnects on its own at the server's ``retry``. Only when the stream cannot be opened at all
// does this read the snapshot every two seconds, and it tries the stream again with a backoff.
// Every event is checked against the generated contract before it is shown.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { ResearchLive } from "@/lib/gen/api.gen";

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
    let closed = false;
    let source: EventSource | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoff = RETRY_MIN_MS;
    let stale: ReturnType<typeof setTimeout> | null = null;
    const fresh = () => { if (stale) { clearTimeout(stale); stale = null; } };

    const read = async () => {
      const mine = seq.current;
      try {
        const next = await api<ResearchLive>("GET", `/v1/research/live?selected=${sel(selected)}`);
        if (!closed && seq.current === mine) apply(next);
      } catch {
        /* the next read tries again */
      }
    };
    const stopPolling = () => { if (poll) { clearInterval(poll); poll = null; } };
    const open = () => {
      if (closed) return;
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
        if (!poll) { void read(); poll = setInterval(() => void read(), POLL_MS); }
        retry = setTimeout(open, backoff);
        backoff = Math.min(backoff * 2, RETRY_MAX_MS);
      };
    };
    open();
    return () => {
      closed = true;
      source?.close();
      stopPolling();
      fresh();
      if (retry) clearTimeout(retry);
    };
  }, [selected, epoch, apply]);

  return { data, state, restart };
}
