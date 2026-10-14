"use client";
// The error screen of a route segment (Next.js error.tsx): the same card every screen uses for
// a failure, with a reference the person can quote and a button that tries the segment again.
// Nothing about the error itself is shown: its message can name internals.
import { m } from "@/lib/messages";
import { StateCard } from "@/ui/design";

export function RouteError({ error, reset, admin = false }: { error: Error & { digest?: string }; reset: () => void; admin?: boolean }) {
  const reference = error.digest ? error.digest.slice(0, 8) : null;
  return (
    <div className={admin ? "pg admin" : "pg"} role="alert">
      <StateCard
        title={m.state.errorTitle}
        body={reference ? `${m.state.errorBody} ${m.state.reference(reference)}.` : m.state.errorBody}
        action={<button type="button" className="btn" onClick={reset}>{m.state.retry}</button>}
      />
    </div>
  );
}
