"use client";

import { Inbox } from "lucide-react";
import type { ApiError } from "@/lib/api";
import { Banner, EmptyState } from "@/ui/primitives";

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
  if (loading) return <p className="row ink-2" aria-live="polite"><span className="spinner" aria-hidden="true" />Loading from the operator API</p>;
  if (error) {
    return (
      <Banner
        kind="error"
        title={error.status === 403 ? "Access is restricted" : error.status === 503 ? "Operator API unavailable" : "Request failed"}
        action={<button className="btn sm" type="button" onClick={onRetry}>Try again</button>}
      >
        {error.detail}
      </Banner>
    );
  }
  if (empty) return <EmptyState title="No rows returned" body="The operator API returned an empty result." icon={<Inbox size={20} strokeWidth={1.8} />} />;
  return null;
}
