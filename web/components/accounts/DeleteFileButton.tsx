"use client";
// Delete one uploaded file of an account, after asking: it is removed from the workspace with
// everything read from it.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export function DeleteFileButton({ documentId, name }: { documentId: string; name: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = async () => {
    if (!window.confirm(`Delete ${name}? It is removed from your workspace with everything read from it.`)) return;
    setBusy(true);
    setError(null);
    try {
      await api("DELETE", `/v1/documents/${documentId}`);
      router.refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) router.refresh();
      else setError("It could not be deleted. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button type="button" className="link-danger sm-text align-start" aria-label={`Delete ${name}`} disabled={busy} onClick={() => void remove()}>
        {busy ? "Deleting" : "Delete"}
      </button>
      {error ? <span className="sub bad-text" role="alert">{error}</span> : null}
    </>
  );
}
