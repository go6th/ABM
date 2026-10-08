"use client";

import type { ApiError } from "@/lib/api";

export function OperatorRequestState({
  loading,
  error,
  empty,
  onRetry,
}: {
  loading: boolean;
  error: ApiError | null;
  empty?: boolean;
  onRetry: () => void;
}) {
  if (loading) return <div className="operator-gate-loading" aria-live="polite"><span className="spinner" /> Loading from the operator API…</div>;
  if (error) {
    return (
      <div className="operator-gate-message" role="alert">
        <strong>{error.status === 403 ? "Access is restricted" : error.status === 503 ? "Operator API unavailable" : "Request failed"}</strong>
        <span>{error.detail}</span>
        <button className="btn sm" type="button" onClick={onRetry}>Try again</button>
      </div>
    );
  }
  if (empty) return <div className="empty"><span className="empty-icon" aria-hidden="true">⌕</span><strong>No rows returned</strong><p>The operator API returned an empty result.</p></div>;
  return null;
}

export function DevelopmentOnlyNotice({ title, detail }: { title: string; detail: string }) {
  if (process.env.NODE_ENV === "production") return null;
  return (
    <aside className="operator-unconnected" role="note">
      <span className="operator-unconnected-mark" aria-hidden="true">i</span>
      <div><strong>{title}</strong><p>{detail}</p></div>
      <span className="badge tone-butter">Not connected</span>
    </aside>
  );
}
