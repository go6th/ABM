"use client";
// The loading and error frame shared by the admin console screens.
import type { ApiError } from "@/lib/api";
import { Skeleton, StateCard } from "@/ui/design";

export function ConsoleLoad({ title, state }: { title: string; state: { error: ApiError | null; reload: () => void } }) {
  return (
    <div className="pg admin">
      <h1>{title}</h1>
      {state.error ? (
        <StateCard
          title={state.error.status === 403 ? "Operators only" : `${title} is unavailable right now`}
          body={state.error.status === 403 ? "This screen needs the operator role." : "Try again in a moment."}
          action={<button type="button" className="btn" onClick={state.reload}>Try again</button>}
        />
      ) : (
        <div className="stack-12" aria-busy="true"><Skeleton kind="block" /><Skeleton kind="row" /><Skeleton kind="row" /></div>
      )}
    </div>
  );
}
