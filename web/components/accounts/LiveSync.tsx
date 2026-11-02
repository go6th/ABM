"use client";
// The account page keeps itself current as the account's files are read (owner request
// 2026-11-02: uploading a work zip did not update the Deep dive counts, Files and Your uploaded
// files on the right until the page was reloaded).
//
// The page is drawn on the server, so nothing on it changed by itself. This island, rendered with
// the uploads, does three cheap things:
//   * while any upload is still being read (uploading, scanning, parsing, indexing) it asks for the
//     document list every few seconds and re-draws the page the moment a file's stage or passage
//     count changes (the list is one small query; the page itself is drawn only on a change);
//   * when the last file finishes, it re-draws the page again a few times (5 s, 20 s, 60 s):
//     the rows a work zip holds (people, postings, technologies) are imported just after
//     indexing, so the counts arrive a little after "Ready";
//   * on arrival it compares the list with what the page was drawn from, so a page the browser
//     restored from its history (Back after an upload on another screen) is re-drawn at once;
//     the same when the tab comes back into view, at most every 15 seconds.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef } from "react";
import { api } from "@/lib/api";
import { isWorking, stageOf } from "@/lib/documents";
import type { DocumentList, DocumentStatus } from "@/lib/gen/api.gen";

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

export function LiveSync({ accountId, docs }: { accountId: string; docs: Doc[] }) {
  const router = useRouter();
  const working = docs.some((d) => isWorking(stageOf(d)));
  const signature = docsSignature(docs);
  const shown = useRef(signature);
  const drawn = useRef(0); // when the page was last drawn (set on arrival and on each re-draw)
  const wasWorking = useRef(working);

  // the page was drawn again from newer files: that is what it shows now
  useEffect(() => {
    shown.current = signature;
    drawn.current = Date.now();
  }, [signature]);

  // ask for the list; a change from what the page shows re-draws the page
  const check = useCallback(async (): Promise<void> => {
    try {
      const list = await api<DocumentList>("GET", `/v1/documents?account_id=${encodeURIComponent(accountId)}`);
      const next = docsSignature(list.documents);
      if (next !== shown.current) {
        shown.current = next;
        drawn.current = Date.now();
        router.refresh();
      }
    } catch { /* the next check tries again */ }
  }, [accountId, router]);

  // on arrival: a page restored from the browser's history may be older than the files
  useEffect(() => { void check(); }, [check]);

  // while a file is being read: check every few seconds
  useEffect(() => {
    if (!working) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const began = Date.now();
    const next = () => {
      const wait = pollEvery(Date.now() - began);
      if (wait !== null && !stopped) timer = setTimeout(tick, wait);
    };
    const tick = async () => {
      await check();
      next();
    };
    next();
    return () => { stopped = true; clearTimeout(timer); };
  }, [working, check]);

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

/** Shown in place of the uploads when the list could not be read (a server error while the page
 * was re-drawn): the page tries once more after 15 seconds instead of staying without its sync. */
export function RetryRefresh() {
  const router = useRouter();
  useEffect(() => {
    const timer = setTimeout(() => router.refresh(), 15000);
    return () => clearTimeout(timer);
  }, [router]);
  return null;
}
