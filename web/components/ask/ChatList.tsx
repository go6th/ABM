"use client";
// Every chat, newest first: open one, or delete it after asking in a pop-up. The sidebar shows only the six
// most recent; this is where the rest are.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError, errorText } from "@/lib/api";
import type { ChatThreadSummary } from "@/lib/gen/api.gen";
import { lastActive } from "@/lib/console";
import { ConfirmDialog } from "@/ui/Modal";
import { announce } from "@/ui/Toaster";

export function ChatList({ threads }: { threads: ChatThreadSummary[] }) {
  const router = useRouter();
  const [asking, setAsking] = useState<ChatThreadSummary | null>(null);
  const remove = async (t: ChatThreadSummary) => {
    try {
      await api("DELETE", `/v1/chat/threads/${encodeURIComponent(t.id)}`);
    } catch (cause) {
      if (!(cause instanceof ApiError && cause.status === 404)) { // 404: already gone
        throw new Error(errorText(cause, "The chat could not be deleted. Try again in a moment."));
      }
    }
    announce("Chat deleted");
    router.refresh();
  };
  return (
    <section className="card list" aria-labelledby="chats-title">
      <h2 id="chats-title" className="card-title lrow">Your chats</h2>
      {threads.length ? threads.map((t) => (
        <div key={t.id} className="lrow">
          <span className="grow">
            <Link href={`/?thread=${encodeURIComponent(t.id)}`} prefetch={false}>{t.title || "Untitled chat"}</Link>
            <span className="sub mut xs-text">{t.turn_count} question{t.turn_count === 1 ? "" : "s"}</span>
          </span>
          <span className="when">{lastActive(t.updated_at)}</span>
          <button type="button" className="link-danger sm-text" aria-label={`Delete ${t.title || "Untitled chat"}`} onClick={() => setAsking(t)}>
            Delete
          </button>
        </div>
      )) : <div className="lrow mut">No chats yet. Ask a question to start one.</div>}
      {asking ? (
        <ConfirmDialog
          title="Delete this chat?"
          body={<>Delete the chat <b>{asking.title || "Untitled chat"}</b>? Its questions and answers are removed.</>}
          confirm="Delete"
          onConfirm={() => remove(asking)}
          onClose={() => setAsking(null)}
        />
      ) : null}
    </section>
  );
}
