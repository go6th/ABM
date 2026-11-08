"use client";
// The error screen of a route segment (Next.js error.tsx): the same card every screen uses for
// a failure, with a reference the person can quote and a button that tries the segment again.
// Reload uses Next's ``retry`` (fetch the segment again, then redraw): ``reset`` alone only
// redraws what the server already sent, so a server error came straight back. Where ``retry`` is
// absent (the operator screens) the page is refreshed, then reset. Nothing about the error itself
// is shown: its message can name internals.
import { useRouter } from "next/navigation";
import { startTransition } from "react";
import { m } from "@/lib/messages";
import { StateCard } from "@/ui/design";

export function RouteError({ error, reset, retry, admin = false }: { error: Error & { digest?: string }; reset: () => void; retry?: () => void; admin?: boolean }) {
  const router = useRouter();
  const reference = error.digest ? error.digest.slice(0, 8) : null;
  const again = () => {
    if (retry) { retry(); return; }
    startTransition(() => { router.refresh(); reset(); });
  };
  return (
    <div className={admin ? "pg admin" : "pg"} role="alert">
      <StateCard
        title={m.state.errorTitle}
        body={reference ? `${m.state.errorBody} ${m.state.reference(reference)}.` : m.state.errorBody}
        action={<button type="button" className="btn" onClick={again}>{m.state.retry}</button>}
      />
    </div>
  );
}
