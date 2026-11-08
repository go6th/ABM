"use client";
// Short notes after an action finishes ("Refresh started", "Chat deleted"), so a click that
// changes something out of view still shows it worked (2026-11-07). ``announce`` can be called
// from any client component; the Toaster in the client shell shows one note at a time for 4 s in
// a polite live region, or in an alert region for failures.
import { useEffect, useState } from "react";

const EVENT = "ui:announce";

export interface Announcement { text: string; tone?: "ok" | "error" }

export function announce(text: string, tone: Announcement["tone"] = "ok"): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<Announcement>(EVENT, { detail: { text, tone } }));
}

export function Toaster() {
  const [note, setNote] = useState<(Announcement & { at: number }) | null>(null);
  useEffect(() => {
    const on = (event: Event) => setNote({ ...(event as CustomEvent<Announcement>).detail, at: Date.now() });
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  useEffect(() => {
    if (!note) return;
    const timer = setTimeout(() => setNote(null), 4000);
    return () => clearTimeout(timer);
  }, [note]);
  const error = note?.tone === "error";
  return (
    <>
      <div className="toast-region" role="status" aria-live="polite">
        {note && !error ? <p key={note.at} className="toast ok">{note.text}</p> : null}
      </div>
      <div className="toast-region" role="alert">
        {note && error ? <p key={note.at} className="toast error">{note.text}</p> : null}
      </div>
    </>
  );
}
