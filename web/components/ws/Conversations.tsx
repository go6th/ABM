"use client";
// Conversations: past threads on the left, the open thread in the reading column. The URL holds
// the open thread (?t=), so a link reopens it.
import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ChatThread, ChatThreads } from "@/lib/gen/api.gen";
import { m } from "@/lib/messages";
import { cellText } from "@/lib/render";
import { Conversation, type Turn } from "./Conversation";

function toTurns(thread: ChatThread): Turn[] {
  return [...thread.turns].sort((a, b) => a.turn_number - b.turn_number).map((t) => ({
    id: t.id,
    question: t.user_content,
    answer: t.assistant_content ?? "",
    status: t.status === "completed" ? "done" : t.status === "failed" ? "failed" : "stopped",
  }));
}

export function Conversations() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const open = params.get("t");
  const [threads, setThreads] = useState<ChatThreads["threads"]>([]);
  const [turns, setTurns] = useState<{ id: string | null; turns: Turn[] }>({ id: null, turns: [] });
  // A thread created by the open conversation keeps its component (and its live stream).
  const [adopted, setAdopted] = useState<string | null>(null);
  const [fresh, setFresh] = useState(0);

  const loadList = useCallback(() => {
    api<ChatThreads>("GET", "/v1/chat/threads").then((r) => setThreads(r.threads)).catch(() => undefined);
  }, []);
  useEffect(loadList, [loadList]);
  useEffect(() => {
    if (!open || open === adopted) return;
    let alive = true;
    api<ChatThread>("GET", `/v1/chat/threads/${encodeURIComponent(open)}`)
      .then((t) => { if (alive) setTurns({ id: t.id, turns: toTurns(t) }); })
      .catch(() => { if (alive) router.replace(pathname); });
    return () => { alive = false; };
  }, [open, adopted, pathname, router]);

  const go = (id: string | null) => router.replace(id ? `${pathname}?t=${encodeURIComponent(id)}` : pathname, { scroll: false });
  const remove = async (id: string) => {
    await api("DELETE", `/v1/chat/threads/${encodeURIComponent(id)}`).catch(() => undefined);
    if (open === id) go(null);
    loadList();
  };
  const live = !open || open === adopted;
  const ready = live || turns.id === open;
  const initial = useMemo(() => (!live && ready ? turns.turns : []), [live, ready, turns]);

  return (
    <div className="ws-convs">
      <nav aria-label={m.conversation.title}>
        <button type="button" className="ws-btn convs-new" onClick={() => { setAdopted(null); setFresh((n) => n + 1); go(null); }}>
          <Plus size={16} strokeWidth={1.8} aria-hidden="true" />{m.conversation.newChat}
        </button>
        {threads.length === 0 ? <p className="ws-small ws-muted convs-empty">{m.conversation.empty}</p> : null}
        {threads.map((t) => (
          <div key={t.id} className="convs-item">
            <button type="button" className="item" aria-current={open === t.id} onClick={() => go(t.id)}>{cellText(t.title, 80)}</button>
            <button type="button" className="ws-icon-btn" aria-label={`${m.conversation.remove}: ${cellText(t.title, 80)}`} onClick={() => void remove(t.id)}>
              <Trash2 size={15} strokeWidth={1.6} aria-hidden="true" />
            </button>
          </div>
        ))}
      </nav>
      <div className="ws-column convs-main">
        {ready ? (
          <Conversation
            key={live ? `fresh-${fresh}` : open ?? "new"}
            threadId={live ? null : open}
            initialTurns={initial}
            placeholder={m.composer.allAccounts}
            onThread={(id) => { setAdopted(id); go(id); loadList(); }}
            empty={<h1 className="ws-display convs-title">{m.home.title}</h1>}
          />
        ) : <span className="ws-skel title" />}
      </div>
    </div>
  );
}
