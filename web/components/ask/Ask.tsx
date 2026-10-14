"use client";
// Ask (home) as designed. Empty: "What do you want to know?" and the composer (no sample
// questions: owner decision 2026-10-14). Thread: the navy question bubble, "Searching your
// workspace" until the first checked sentence arrives, then the answer as it streams (citation
// numbers shown but not yet clickable), then the finished answer with citations; the follow-up
// composer sits at the bottom and "What this answer used" to the right on wide screens and under
// the latest answer otherwise. If the model's reply fails its checks the partial text is cleared
// and the workspace's own answer streams instead. A failed question stays on screen with its
// error and a Retry. The thread id goes into the address with history.replaceState, so the
// component is never remounted mid-chat.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiStream, ApiError } from "@/lib/api";
import type { ChatAnswer } from "@/lib/gen/api.gen";
import { addPieces, readFrames, type StreamPart } from "@/lib/sse";
import { NEW_CHAT_EVENT } from "@/components/ws/WorkspaceShell";
import { AnswerView, StreamingAnswer, UsedPanel } from "./AnswerView";
import { AskComposer, type ScopeAccount } from "./AskComposer";

export interface AskTurn {
  key: string;
  question: string;
  status: "searching" | "done" | "failed";
  answer?: ChatAnswer;
  partial?: StreamPart[][];
  error?: string;
}

const PLACEHOLDER = "Ask about your accounts";

function failureText(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 404) return "That account or chat is no longer in your workspace.";
    if (cause.status === 401) return "Your session ended. Sign in again to ask.";
    if (cause.status === 429) return "Too many questions at once. Wait a moment and try again.";
  }
  return "The answer could not be completed. Try again.";
}

export function Ask({
  accounts, threadId: initialThread, initialTurns, initialScope, initialQuestion,
}: {
  accounts: ScopeAccount[];
  threadId: string | null;
  initialTurns: AskTurn[];
  initialScope: ScopeAccount | null;
  initialQuestion: string | null;
}) {
  const router = useRouter();
  const [threadId, setThreadId] = useState<string | null>(initialThread);
  const [turns, setTurns] = useState<AskTurn[]>(initialTurns);
  const [draft, setDraft] = useState("");
  const [scope, setScope] = useState<ScopeAccount | null>(initialScope);
  const [open, setOpen] = useState<{ turn: string; n: number } | null>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<string | null>(null);
  const [webHint, setWebHint] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const asked = useRef(false);
  const busy = turns.some((t) => t.status === "searching");

  // A different thread opened from Recent: load it (our own new thread is already shown). The
  // state follows the prop during render; the effect only stops the stream of the old thread.
  const [shownThread, setShownThread] = useState<string | null>(initialThread);
  const [switches, setSwitches] = useState(0);
  if (initialThread !== shownThread) {
    setShownThread(initialThread);
    if (initialThread && initialThread !== threadId) {
      setThreadId(initialThread);
      setTurns(initialTurns);
      setOpen(null);
      setActive(null);
      setSwitches((n) => n + 1);
    }
  }
  useEffect(() => {
    if (switches) abort.current?.abort();
  }, [switches]);

  useEffect(() => {
    const reset = () => {
      abort.current?.abort();
      setThreadId(null);
      setTurns([]);
      setDraft("");
      setOpen(null);
      setActive(null);
      setDismissed(new Set());
      setWebHint(false);
    };
    window.addEventListener(NEW_CHAT_EVENT, reset);
    return () => { window.removeEventListener(NEW_CHAT_EVENT, reset); abort.current?.abort(); };
  }, []);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const patch = (key: string, change: Partial<AskTurn>) =>
    setTurns((all) => all.map((t) => (t.key === key ? { ...t, ...change } : t)));

  const send = useCallback(async (question: string, retryKey?: string) => {
    const text = question.trim();
    if (!text || busy) return;
    const key = retryKey ?? crypto.randomUUID();
    if (retryKey) patch(retryKey, { status: "searching", error: undefined, partial: undefined });
    else setTurns((all) => [...all, { key, question: text, status: "searching" }]);
    setDraft("");
    setWebHint(false);
    const controller = new AbortController();
    abort.current = controller;
    let got = false;
    let created: string | null = null;
    try {
      const reply = await apiStream("/v1/chat/ask", {
        request_id: key,
        question: text,
        thread_id: threadId,
        account_id: scope?.id ?? null,
      }, controller.signal);
      await readFrames(reply, (frame) => {
        if (frame.event === "thread") {
          const id = String(frame.data.thread_id ?? "");
          if (id) {
            setThreadId(id);
            if (frame.data.created) created = id;
          }
        } else if (frame.event === "text") {
          setTurns((all) => all.map((t) => (t.key === key ? { ...t, partial: addPieces(t.partial ?? [], frame.data) } : t)));
        } else if (frame.event === "reset") {
          patch(key, { partial: undefined });
        } else if (frame.event === "answer") {
          got = true;
          patch(key, { status: "done", answer: frame.data as unknown as ChatAnswer, partial: undefined });
          setActive(key);
        } else if (frame.event === "error" || frame.event === "invalid") {
          throw new ApiError(502, "answer_failed", "");
        }
      });
      if (!got) throw new ApiError(502, "answer_incomplete", "");
      if (created) {
        window.history.replaceState(null, "", `/?thread=${encodeURIComponent(created)}`);
        router.refresh();
      }
    } catch (cause) {
      if (controller.signal.aborted) return;
      patch(key, { status: "failed", error: failureText(cause), partial: undefined });
    }
  }, [busy, threadId, scope, router]);

  // ?q= from an account page: ask once on arrival.
  useEffect(() => {
    if (initialQuestion && !asked.current) {
      asked.current = true;
      void send(initialQuestion);
    }
  }, [initialQuestion, send]);

  const panelTurn = turns.find((t) => t.key === active && t.answer) ?? [...turns].reverse().find((t) => t.answer);
  const lastDone = [...turns].reverse().find((t) => t.answer);
  const pickAccount = () => setWebHint(true);
  const composerProps = {
    draft, onDraft: setDraft, busy, accounts, scope, onScope: setScope,
    webHintOpen: webHint, onWebHint: setWebHint, onSend: () => void send(draft),
  };

  if (!turns.length) {
    return (
      <div className="ask">
        <div className="ask-empty">
          <div className="hero">
            <h1>What do you want to know?</h1>
            <p className="lead">
              Ask across your {accounts.length} account{accounts.length === 1 ? "" : "s"}. Answers come from your workspace unless you choose the web.
            </p>
          </div>
          <AskComposer variant="hero" placeholder={PLACEHOLDER} menuDown {...composerProps} />
        </div>
      </div>
    );
  }

  return (
    <div className="ask">
      <div className="ask-scroll" ref={scroller}>
        <div className="ask-cols">
          <div className="ask-msgs" aria-live="polite">
            {turns.map((t) => (
              <div key={t.key} className="reply">
                <div className="bubble">{t.question}</div>
                {t.status === "searching" && t.partial?.length ? <StreamingAnswer paragraphs={t.partial} /> : null}
                {t.status === "searching" && !t.partial?.length ? (
                  <div className="searching" role="status"><span className="pdot" aria-hidden="true" />Searching your workspace</div>
                ) : null}
                {t.status === "failed" ? (
                  <div className="notice error turn-error" role="alert">
                    <p>{t.error}</p>
                    <button type="button" className="btn sm" disabled={busy} onClick={() => void send(t.question, t.key)}>Retry</button>
                  </div>
                ) : null}
                {t.answer ? (
                  <AnswerView
                    answer={t.answer}
                    openSource={open?.turn === t.key ? open.n : null}
                    onOpenSource={(n) => { setOpen(n === null ? null : { turn: t.key, n }); setActive(t.key); }}
                    offerDismissed={dismissed.has(t.key)}
                    onDismissOffer={() => setDismissed((d) => new Set(d).add(t.key))}
                    onFollowUp={(q) => void send(q)}
                    onPickAccount={pickAccount}
                    busy={busy}
                  />
                ) : null}
                {t.answer && t.key === lastDone?.key ? (
                  <div className="used-inline">
                    <UsedPanel answer={t.answer} onOpenSource={(n) => setOpen({ turn: t.key, n })} />
                  </div>
                ) : null}
              </div>
            ))}
          </div>
          <aside className="ask-side" aria-label="What this answer used">
            {panelTurn?.answer ? (
              <UsedPanel answer={panelTurn.answer} onOpenSource={(n) => setOpen({ turn: panelTurn.key, n })} />
            ) : null}
          </aside>
        </div>
      </div>
      <div className="ask-dock">
        <div className="ask-cols">
          <AskComposer variant="follow" placeholder="Ask a follow up" {...composerProps} />
          <div className="ask-side" aria-hidden="true" />
        </div>
      </div>
    </div>
  );
}
