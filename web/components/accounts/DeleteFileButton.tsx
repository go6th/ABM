"use client";
// Delete one uploaded file of an account, after asking in a pop-up: it is removed from the
// workspace with everything read from it. A failure stays in the pop-up with its reason.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError, errorText } from "@/lib/api";
import { ConfirmDialog } from "@/ui/Modal";
import { announce } from "@/ui/Toaster";

export function DeleteFileButton({ documentId, name }: { documentId: string; name: string }) {
  const router = useRouter();
  const [asking, setAsking] = useState(false);
  const remove = async () => {
    try {
      await api("DELETE", `/v1/documents/${documentId}`);
    } catch (cause) {
      if (!(cause instanceof ApiError && cause.status === 404)) { // 404: already gone
        throw new Error(errorText(cause, "It could not be deleted. Try again in a moment."));
      }
    }
    announce("File removed");
    router.refresh();
  };
  return (
    <>
      <button type="button" className="link-danger sm-text align-start" aria-label={`Delete ${name}`} onClick={() => setAsking(true)}>
        Delete
      </button>
      {asking ? (
        <ConfirmDialog
          title="Delete this file?"
          body={<>Delete <b>{name}</b>? It is removed from your workspace with everything read from it.</>}
          confirm="Delete"
          onConfirm={remove}
          onClose={() => setAsking(false)}
        />
      ) : null}
    </>
  );
}
