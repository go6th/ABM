"use client";
// Whether this tab is on screen. A hidden tab does no background reading (research progress, the
// file lists, the Research board): each reader stops while this is false, reads once when the tab
// comes back and carries on. The server render counts as visible, so nothing flickers on load.
import { useSyncExternalStore } from "react";

/** True unless the tab is hidden ("prerender" and an older browser without the API count as visible). */
export function isVisible(): boolean {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

function subscribe(onChange: () => void): () => void {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

export function useVisible(): boolean {
  return useSyncExternalStore(subscribe, isVisible, () => true);
}
