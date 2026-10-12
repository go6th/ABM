"use client";
// One conversation: the question as a tinted bubble, the answer as reading text, streamed from the backend's
// first-party event stream. Model output is untrusted: markdown without raw HTML, only http(s)
// links, opened with noopener noreferrer nofollow. Question text is never logged or stored by
// the browser; it goes only to the API.
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy, Download, MessageSquare } from "lucide-react";
import { api, apiStream, ApiError } from "@/lib/api";
import { LINK_REL, safeLink } from "@/lib/render";
import { m } from "@/lib/messages";
import { Composer } from "./Composer";

export interface Turn { id: string; question: string; answer: string; status: "streaming" | "done" | "stopped" | "failed" }

interface Frame { event: string; data: Record<string, unknown> }

export function parseFrame(raw: string): Frame | null {
  let event = "message";
  const data: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try {
    const value: unknown = JSON.parse(data.join("\n"));
    return typeof value === "object" && value !== null ? { event, data: value as Record<string, unknown> } : null;
  } catch {
    return null;
  }
}

export async function readFrames(reply: Response, onFrame: (frame: Frame) => void): Promise<void> {
  const reader = reply.body?.getReader();
  if (!reader) throw new ApiError(502, "stream_unavailable", "");
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = parseFrame(buffer.slice(0, boundary));
      if (frame) onFrame(frame);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
}

const NO_TURNS: Turn[] = [];

const markdown: Components = {
  a: ({ href, children }) => {
    const link = safeLink(href);
    return link ? <a href={link} target="_blank" rel={`${LINK_REL} nofollow`}>{children}</a> : <span>{children}</span>;
  },
  img: () => null,
};

export function Answer({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <div className={streaming ? "a prose streaming" : "a prose"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={markdown}>{text}</ReactMarkdown>
      {streaming && !text ? <p /> : null}
    </div>
  );
}

function AnswerTools({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "answer.md";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="answer-tools">
      <button
        type="button"
        className="icon-btn"
        aria-label={copied ? m.conversation.copied : m.conversation.copy}
        onClick={() => void navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1600); })}
      >
        {copied ? <Check size={16} strokeWidth={1.6} /> : <Copy size={16} strokeWidth={1.6} />}
      </button>
      <button type="button" className="icon-btn" aria-label={m.conversation.download} onClick={download}>
        <Download size={16} strokeWidth={1.6} />
      </button>
    </div>
  );
}

function failure(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 429) return m.conversation.limit;
    if (cause.status === 503) return m.conversation.unavailable;
    if (cause.type === "out_of_context" || cause.status === 422) return m.conversation.outOfScope;
  }
  return m.conversation.failed;
}

export function Conversation({
  threadId: initialThread = null,
  initialTurns = NO_TURNS,
  placeholder,
  empty,
  suggestions,
  onThread,
  onTurnEnd,
  largeComposer = false,
}: {
  threadId?: string | null;
  initialTurns?: Turn[];
  placeholder: string;
  empty?: React.ReactNode;
  suggestions?: readonly string[];
  onThread?: (id: string) => void;
  /** Called when a turn ends (done, stopped or failed); the thread's title exists from then. */
  onTurnEnd?: () => void;
  largeComposer?: boolean;
}) {
  const [threadId, setThreadId] = useState<string | null>(initialThread);
  const [turns, setTurns] = useState<Turn[]>(initialTurns);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const streaming = turns.some((t) => t.status === "streaming");

  useEffect(() => () => abort.current?.abort(), []);

  const patch = (id: string, change: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...(typeof change === "function" ? change(t) : change) } : t)));

  const ask = useCallback(async (question: string) => {
    const text = question.trim();
    if (!text || streaming) return;
    setError(null);
    setDraft("");
    const id = crypto.randomUUID();
    setTurns((all) => [...all, { id, question: text, answer: "", status: "streaming" }]);
    const controller = new AbortController();
    abort.current = controller;
    try {
      let active = threadId;
      if (!active) {
        const created = await api<{ id: string }>("POST", "/v1/chat/threads", {});
        active = created.id;
        setThreadId(active);
        onThread?.(active);
      }
      const reply = await apiStream(`/v1/chat/threads/${encodeURIComponent(active)}/turns/stream`, {
        request_id: id, content: text, enforce_project_scope: true, fresh_output: true,
      }, controller.signal);
      await readFrames(reply, (frame) => {
        if (frame.event === "text" && typeof frame.data.text === "string") {
          const delta = frame.data.text;
          patch(id, (t) => ({ answer: t.answer + delta }));
        }
        if (frame.event === "error") throw new ApiError(502, "stream_error", "");
        if (frame.event === "done") patch(id, { status: "done" });
      });
      patch(id, (t) => (t.status === "streaming" ? { status: "done" } : {}));
    } catch (cause) {
      if (controller.signal.aborted) {
        patch(id, { status: "stopped" });
        return;
      }
      setTurns((all) => all.filter((t) => t.id !== id || t.answer.length > 0).map((t) => (t.id === id ? { ...t, status: "failed" } : t)));
      setError(failure(cause));
      setDraft(text);
    } finally {
      abort.current = null;
      onTurnEnd?.();
    }
  }, [streaming, threadId, onThread, onTurnEnd]);

  return (
    <>
      {turns.length === 0 ? empty : null}
      {turns.length === 0 && suggestions?.length ? (
        <div className="suggest">
          {suggestions.map((q) => (
            <button key={q} type="button" onClick={() => void ask(q)}>
              <MessageSquare size={16} strokeWidth={1.6} aria-hidden="true" />{q}
            </button>
          ))}
        </div>
      ) : null}
      {turns.length > 0 ? (
        <div className="thread">
          {turns.map((t) => (
            <article key={t.id} className="turn" aria-busy={t.status === "streaming"}>
              <p className="q"><span className="sr-only">{m.conversation.you}: </span>{t.question}</p>
              <div aria-live="polite">
                {t.answer || t.status === "streaming" ? <Answer text={t.answer} streaming={t.status === "streaming"} /> : null}
              </div>
              {t.status === "streaming" && !t.answer ? <p className="t-footnote ink-2">{m.conversation.streaming}</p> : null}
              {t.status === "stopped" ? <p className="t-footnote ink-2">{m.conversation.stopped}</p> : null}
              {t.status === "done" && t.answer ? <AnswerTools text={t.answer} /> : null}
            </article>
          ))}
        </div>
      ) : null}
      <div className="dock">
        {error ? <div className="banner error" role="alert"><span className="grow">{error}</span></div> : null}
        <div className={error ? "gap-top" : undefined}>
          <Composer
            value={draft}
            onChange={setDraft}
            onSend={() => void ask(draft)}
            onStop={() => abort.current?.abort()}
            streaming={streaming}
            placeholder={placeholder}
            large={largeComposer && turns.length === 0}
            autoFocus={largeComposer}
          />
        </div>
      </div>
    </>
  );
}
