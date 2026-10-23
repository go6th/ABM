"use client";
// Every chat, newest first: open one, or delete it after asking. The sidebar shows only the six
// most recent; this is where the rest are.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ChatThreadSummary } from "@/lib/gen/api.gen";
import { lastActive } from "@/lib/console";

export function ChatList({ threads }: { threads: ChatThreadSummary[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const remove = async (t: ChatThreadSummary) => {
    if (!window.confirm(`Delete the chat "${t.title || "Untitled chat"}"? Its questions and answers are removed.`)) return;
    setBusy(t.id);
    setError(null);
    try {
      await api("DELETE", `/v1/chat/threads/${encodeURIComponent(t.id)}`);
      router.refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) router.refresh();
      else setError("The chat could not be deleted. Try again.");
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="card list" aria-labelledby="chats-title">
      <h2 id="chats-title" className="card-title lrow">Your chats</h2>
      {error ? <p className="lrow sm-text bad-text" role="alert">{error}</p> : null}
      {threads.length ? threads.map((t) => (
        <div key={t.id} className="lrow">
          <span className="grow">
            <Link href={`/?thread=${encodeURIComponent(t.id)}`} prefetch={false}>{t.title || "Untitled chat"}</Link>
            <span className="sub mut xs-text">{t.turn_count} question{t.turn_count === 1 ? "" : "s"}</span>
          </span>
          <span className="when">{lastActive(t.updated_at)}</span>
          <button type="button" className="link-danger sm-text" aria-label={`Delete ${t.title || "Untitled chat"}`} disabled={busy === t.id} onClick={() => void remove(t)}>
            {busy === t.id ? "Deleting" : "Delete"}
          </button>
        </div>
      )) : <div className="lrow mut">No chats yet. Ask a question to start one.</div>}
    </section>
  );
}
