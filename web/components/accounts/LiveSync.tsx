"use client";
// The account page keeps itself current as the account's files are read (owner request
// 2026-11-02: uploading a work zip did not update the Deep dive counts, Files and Your uploaded
// files on the right until the page was reloaded).
//
// The page is drawn on the server, so nothing on it changed by itself. This island, rendered with
// the uploads, does three cheap things:
//   * while any upload is still being read (uploading, scanning, parsing, indexing) it asks for the
//     document list every few seconds and re-draws the page only when a file's STAGE changes (the
//     list is one small query; the page itself is drawn only on a change of stage, not on every
//     passage that is read). Progress inside a stage keeps the check going: the 15-minute give-up
//     window starts again whenever anything moves, so a long read is never abandoned while it
//     makes progress. Nothing is asked while the tab is hidden; when it comes back, checking
//     restarts;
//   * when the last file finishes, it re-draws the page again a few times (5 s, 20 s, 60 s):
//     the rows a work zip holds (people, postings, technologies) are imported just after
//     indexing, so the counts arrive a little after "Ready";
//   * on arrival it compares the list with what the page was drawn from, so a page the browser
//     restored from its history (Back after an upload on another screen) is re-drawn at once;
//     the same when the tab comes back into view, at most every 15 seconds.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import { api } from "@/lib/api";
import { isWorking, stageOf } from "@/lib/documents";
import type { DocumentList, DocumentStatus } from "@/lib/gen/api.gen";
import { useVisible } from "@/lib/useVisible";

export const POLL_MS = 4000;
/** An upload that never finishes (the tab was closed mid-upload) must not poll for ever: the
 * interval grows (4 s for a minute, 10 s until five minutes, then 30 s) and stops at 15 minutes. */
export const POLL_GIVE_UP_MS = 15 * 60 * 1000;
export function pollEvery(elapsedMs: number): number | null {
  if (elapsedMs >= POLL_GIVE_UP_MS) return null;
  return elapsedMs < 60_000 ? POLL_MS : elapsedMs < 300_000 ? 10_000 : 30_000;
}
export const AFTER_READY_MS = [5000, 20000, 60000];
export const FOCUS_GAP_MS = 15000;

type Doc = Pick<DocumentStatus, "document_id" | "stage" | "needs_company" | "passages" | "parts_done" | "members_read" | "indexed_passages">;

/** What the page shows about the files: any change in it means the page is out of date. */
export function docsSignature(docs: Doc[]): string {
  return docs.map((d) => `${d.document_id}:${stageOf(d)}:${d.passages ?? 0}:${d.parts_done ?? 0}:${d.members_read ?? 0}:${d.indexed_passages ?? 0}`).join("|");
}

/** Only the files and the stage each is in: a change in it means the page's file lists are out of date. */
export function stagesSignature(docs: Doc[]): string {
  return docs.map((d) => `${d.document_id}:${stageOf(d)}`).join("|");
}

export function LiveSync({ accountId, docs }: { accountId: string; docs: Doc[] }) {
  const router = useRouter();
  const visible = useVisible();
  const working = docs.some((d) => isWorking(stageOf(d)));
  const signature = docsSignature(docs);
  const stages = stagesSignature(docs);
  const shown = useRef(signature); // everything the page was drawn from
  const shownStages = useRef(stages); // the stages the page was drawn from
  const latest = useRef(signature); // the last list read, however far the files have got
  const windowStart = useRef(0); // when the give-up window began (set when checking starts, again on progress)
  const drawn = useRef(0); // when the page was last drawn (set on arrival and on each re-draw)
  const wasWorking = useRef(working);

  // the page was drawn again from newer files: that is what it shows now
  useEffect(() => {
    shown.current = signature;
    shownStages.current = stages;
    latest.current = signature;
    drawn.current = Date.now();
  }, [signature, stages]);

  // ask for the list. On arrival any difference from what the page shows re-draws it (the page may
  // come from the browser's history); while files are read only a change of stage does, and any
  // movement at all gives the file more time before the checking gives up
  const check = useCallback(async (onArrival = false): Promise<void> => {
    try {
      const list = await api<DocumentList>("GET", `/v1/documents?account_id=${encodeURIComponent(accountId)}`);
      const next = docsSignature(list.documents);
      if (next !== latest.current) {
        latest.current = next;
        windowStart.current = Date.now();
      }
      const nextStages = stagesSignature(list.documents);
      if (onArrival ? next !== shown.current : nextStages !== shownStages.current) {
        shown.current = next;
        shownStages.current = nextStages;
        drawn.current = Date.now();
        router.refresh();
      }
    } catch { /* the next check tries again */ }
  }, [accountId, router]);

  // on arrival: a page restored from the browser's history may be older than the files
  useEffect(() => { void check(true); }, [check]);

  // while a file is being read and the tab is on screen: check every few seconds; the window
  // starts again when the tab comes back
  useEffect(() => {
    if (!working || !visible) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    windowStart.current = Date.now();
    const next = () => {
      const wait = pollEvery(Date.now() - windowStart.current);
      if (wait !== null && !stopped) timer = setTimeout(tick, wait);
    };
    const tick = async () => {
      await check();
      next();
    };
    next();
    return () => { stopped = true; clearTimeout(timer); };
  }, [working, visible, check]);

  // the last file finished: the rows of a work zip are imported just after indexing
  useEffect(() => {
    const finished = wasWorking.current && !working;
    wasWorking.current = working;
    if (!finished) return;
    const timers = AFTER_READY_MS.map((ms) => setTimeout(() => { drawn.current = Date.now(); router.refresh(); }, ms));
    return () => timers.forEach(clearTimeout);
  }, [working, router]);

  // back on the tab, or restored from history: the files may have changed elsewhere
  useEffect(() => {
    const again = () => {
      if (Date.now() - drawn.current < FOCUS_GAP_MS) return;
      drawn.current = Date.now();
      router.refresh();
    };
    const onVisible = () => { if (document.visibilityState === "visible") again(); };
    const onShow = (e: PageTransitionEvent) => { if (e.persisted) again(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("pageshow", onShow);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("pageshow", onShow);
    };
  }, [router]);

  return null;
}

/** The wait before the nth try again after a page could not be read: 15 s, 30 s, then 60 s. */
export function retryAfter(attempt: number): number {
  return Math.min(15_000 * 2 ** attempt, 60_000);
}

/** Shown in place of the uploads (or the whole page) when it could not be read, a server error
 * while the page was re-drawn: the page is asked for again after 15 seconds, and again after 30
 * and 60 if that fails too, instead of staying without its live updates. It waits while the tab is
 * hidden. Each try counts as an attempt, so a second failure arms the next one. */
export function RetryRefresh() {
  const router = useRouter();
  const visible = useVisible();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!visible) return;
    const timer = setTimeout(() => {
      router.refresh();
      setAttempt((n) => n + 1);
    }, retryAfter(attempt));
    return () => clearTimeout(timer);
  }, [router, visible, attempt]);
  return null;
}

/** "Try again" for a page that could not be read: asks the server for it again at once. */
export function TryAgain() {
  const router = useRouter();
  const [pending, start] = useTransition();
  return (
    <button type="button" className="btn" aria-busy={pending || undefined} disabled={pending} onClick={() => start(() => router.refresh())}>
      Try again
    </button>
  );
}
