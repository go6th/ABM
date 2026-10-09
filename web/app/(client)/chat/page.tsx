"use client";

import Link from "next/link";
import { ArrowUp, Building2, CheckCircle2, ChevronRight, Loader2, MessageSquarePlus, Trash2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useCallback, useEffect, useRef, useState } from "react";
import { SignedIn } from "@/components/SignedIn";
import { ApiError, api, apiStream } from "@/lib/api";

interface ChatModel {
  id: string;
  provider: string;
  display_name: string;
  input_usd_per_million: string;
  output_usd_per_million: string;
  context_window: number;
  max_output_tokens: number;
  capabilities: { streaming: boolean; vision: boolean; tool_use: boolean };
}

interface ThreadSummary {
  id: string;
  title: string;
  turn_count: number;
  updated_at: string;
  model_id: string | null;
}

interface SavedTurn {
  id: string;
  turn_number: number;
  model_id: string;
  user_content: string;
  assistant_content: string | null;
  status: "reserved" | "completed" | "failed";
}

interface ThreadDetail {
  id: string;
  title: string;
  turns: SavedTurn[];
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface StreamFrame {
  event: string;
  data: Record<string, unknown>;
}

interface ProviderKeyStatus {
  available: boolean;
  configured: boolean;
  updated_at: string | null;
}

interface AccountRunSummary {
  domain: string;
  name: string | null;
  latest_run: { run_id: string; status: string; created_at: string } | null;
}

type PipelineStage = "idle" | "collecting" | "analyzing" | "checking" | "done" | "review";

function ProviderKeyManager({ canManage }: { canManage: boolean }) {
  const [status, setStatus] = useState<ProviderKeyStatus | null>(null);
  const [keyValue, setKeyValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState("");

  useEffect(() => {
    if (!canManage) return;
    let current = true;
    api<ProviderKeyStatus>("GET", "/v1/chat/provider-key").then((value) => {
      if (current) setStatus(value);
    }).catch(() => {
      if (current) setFeedback("Key settings could not be loaded.");
    });
    return () => { current = false; };
  }, [canManage]);

  if (!canManage) return null;

  const save = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!keyValue || saving) return;
    setSaving(true);
    setFeedback("");
    try {
      const value = await api<ProviderKeyStatus>("PUT", "/v1/chat/provider-key", { api_key: keyValue });
      setStatus(value);
      setKeyValue("");
      setFeedback("Your key was encrypted and saved.");
    } catch {
      setFeedback("The key could not be saved. Check it and try again.");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setSaving(true);
    setFeedback("");
    try {
      await api("DELETE", "/v1/chat/provider-key");
      setStatus((previous) => previous ? { ...previous, configured: false, updated_at: null } : previous);
      setFeedback("Your saved key was removed.");
    } catch {
      setFeedback("The saved key could not be removed.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <details className="chat-key-settings">
      <summary>Provider key</summary>
      {status === null ? <p role="status">Loading key settings…</p> : !status.available ? (
        <p>This environment does not enable encrypted tenant keys.</p>
      ) : (
        <>
          <p>{status.configured ? "A tenant key is active for new chats." : "The workspace default key is active."}</p>
          <form onSubmit={(event) => void save(event)}>
            <label htmlFor="tenant-provider-key">Anthropic API key</label>
            <input
              id="tenant-provider-key"
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              value={keyValue}
              onChange={(event) => setKeyValue(event.target.value)}
              placeholder="sk-ant-…"
              maxLength={512}
              disabled={saving}
            />
            <button className="btn primary" type="submit" disabled={saving || !keyValue}>
              Save encrypted key
            </button>
          </form>
          {status.configured ? <button className="chat-remove-key" type="button" onClick={() => void remove()} disabled={saving}>Remove saved key</button> : null}
        </>
      )}
      {feedback ? <p className="chat-key-feedback" role="status">{feedback}</p> : null}
    </details>
  );
}

function parseFrame(raw: string): StreamFrame | null {
  let event = "message";
  const data: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try {
    const value: unknown = JSON.parse(data.join("\n"));
    return typeof value === "object" && value !== null
      ? { event, data: value as Record<string, unknown> }
      : null;
  } catch {
    return null;
  }
}

async function readFrames(reply: Response, onFrame: (frame: StreamFrame) => void) {
  const reader = reply.body?.getReader();
  if (!reader) throw new Error("The chat stream is unavailable.");
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
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

function ChatWorkspace({ canChat, canManageKey }: { canChat: boolean; canManageKey: boolean }) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [accountRuns, setAccountRuns] = useState<AccountRunSummary[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [pipelineStage, setPipelineStage] = useState<PipelineStage>("idle");
  const [runningCost, setRunningCost] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);
  const serial = useRef(0);
  const skipDetailLoad = useRef<string | null>(null);
  const threadIdRef = useRef<string | null>(null);

  useEffect(() => {
    threadIdRef.current = threadId;
  }, [threadId]);

  const refreshThreads = useCallback(async () => {
    const reply = await api<{ threads: ThreadSummary[] }>("GET", "/v1/chat/threads");
    setThreads(reply.threads);
    return reply.threads;
  }, []);

  useEffect(() => {
    let current = true;
    Promise.all([
      api<{ threads: ThreadSummary[] }>("GET", "/v1/chat/threads"),
      api<{ accounts: AccountRunSummary[] }>("GET", "/v1/accounts?limit=8").catch(() => ({ accounts: [] })),
    ]).then(([history, accs]) => {
      if (!current) return;
      setThreads(history.threads);
      setThreadId(history.threads[0]?.id ?? null);
      setAccountRuns(accs.accounts ?? []);
    }).catch(() => {
      if (current) setError("Chat could not be loaded. Refresh the page to try again.");
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, []);

  useEffect(() => {
    if (!threadId) return;
    if (skipDetailLoad.current === threadId) {
      skipDetailLoad.current = null;
      return;
    }
    const request = ++serial.current;
    let current = true;
    api<ThreadDetail>("GET", `/v1/chat/threads/${threadId}`).then((thread) => {
      if (!current || serial.current !== request) return;
      const restored: Message[] = [];
      for (const turn of thread.turns) {
        restored.push({ id: `${turn.id}-user`, role: "user", content: turn.user_content });
        if (turn.assistant_content) {
          restored.push({ id: `${turn.id}-assistant`, role: "assistant", content: turn.assistant_content });
        } else if (turn.status === "failed") {
          restored.push({ id: `${turn.id}-assistant`, role: "assistant", content: "The response could not be completed. You can try again in a new turn." });
        }
      }
      setMessages(restored);
    }).catch(() => {
      if (current) setError("That conversation could not be opened.");
    });
    return () => { current = false; };
  }, [threadId]);

  useEffect(() => {
    const input = textarea.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
  }, [draft]);

  const createThread = async () => {
    const created = await api<{ id: string }>("POST", "/v1/chat/threads", {});
    skipDetailLoad.current = created.id;
    threadIdRef.current = created.id;
    setThreadId(created.id);
    await refreshThreads();
    setMessages([]);
    setError("");
    setPipelineStage("idle");
    setRunningCost(null);
    textarea.current?.focus();
    return created.id;
  };

  const chooseThread = (id: string) => {
    if (sending) return;
    setError("");
    setPipelineStage("idle");
    threadIdRef.current = id;
    setMessages([]);
    setThreadId(id);
  };

  const removeThread = async (id: string) => {
    if (sending) return;
    try {
      await api("DELETE", `/v1/chat/threads/${id}`);
      const remaining = (await refreshThreads()).filter((item) => item.id !== id);
      if (threadIdRef.current === id) {
        threadIdRef.current = remaining[0]?.id ?? null;
        setThreadId(remaining[0]?.id ?? null);
        setMessages([]);
      }
      setError("");
    } catch {
      setError("This conversation could not be deleted.");
    }
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || sending || !canChat) return;
    setDraft("");
    setError("");
    setSending(true);
    setPipelineStage("collecting");
    setNotice("Collecting data…");

    let activeId = threadIdRef.current;
    const userId = crypto.randomUUID();
    const assistantId = crypto.randomUUID();
    try {
      if (!activeId) activeId = await createThread();
      if (!activeId) throw new Error("The conversation could not be created.");
      threadIdRef.current = activeId;
      setMessages((items) => [
        ...items,
        { id: userId, role: "user", content: text },
        { id: assistantId, role: "assistant", content: "" },
      ]);

      const reply = await apiStream(`/v1/chat/threads/${activeId}/turns/stream`, {
        request_id: crypto.randomUUID(),
        content: text,
        enforce_project_scope: true,
        fresh_output: true,
      });

      let received = false;
      await readFrames(reply, (frame) => {
        if (frame.event === "text" && typeof frame.data.text === "string") {
          received = true;
          setPipelineStage("analyzing");
          setNotice("Analyzing and predicting…");
          const delta = frame.data.text;
          setMessages((items) => items.map((item) => item.id === assistantId
            ? { ...item, content: item.content + delta }
            : item));
        }
        if (frame.event === "error") {
          setPipelineStage("review");
          const errMsg = typeof frame.data.message === "string" ? frame.data.message : "The model response could not be completed.";
          throw new Error(errMsg);
        }
        if (frame.event === "done") {
          setPipelineStage("done");
          const cost = typeof frame.data.cost_usd === "string" ? frame.data.cost_usd : null;
          if (cost) setRunningCost(cost);
          setNotice("Done");
        }
      });
      if (!received) setMessages((items) => items.filter((item) => item.id !== assistantId));
      await refreshThreads();
    } catch (cause) {
      setMessages((items) => items.filter((item) => item.id !== assistantId));
      let message = "The response could not be completed. Please try again.";
      if (cause instanceof ApiError) {
        if (cause.status === 429) {
          message = "This conversation reached its usage limit. Start a new chat to continue.";
        } else if (cause.type === "out_of_context" || cause.status === 422) {
          message = cause.detail || cause.message || "Query is out of project context: Please submit a query related to ABM, account intelligence, or company research.";
        } else if (cause.message) {
          message = cause.message;
        }
      } else if (cause instanceof Error && cause.message) {
        message = cause.message;
      }
      setError(message);
      setNotice("");
      setDraft(text);
    } finally {
      setSending(false);
      await refreshThreads().catch(() => undefined);
      textarea.current?.focus();
    }
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send();
    }
  };

  return (
    <div className="chat-workspace">
      <aside className="chat-sidebar" aria-label="Conversation history and workspace navigation">
        <div className="chat-sidebar-heading">
          <span className="chat-sidebar-title">Chats</span>
          <button className="chat-new-button" type="button" onClick={() => void createThread()} disabled={!canChat || sending}>
            <MessageSquarePlus aria-hidden="true" /><span>New chat</span>
          </button>
        </div>
        <h2 className="sr-only">Recent conversations</h2>
        {loading ? <p className="chat-sidebar-state" role="status">Loading conversations…</p> : null}
        {!loading && threads.length === 0 ? <p className="chat-sidebar-state">Your conversations will appear here.</p> : null}
        <ul className="chat-thread-list">
          {threads.map((item) => (
            <li key={item.id} className={item.id === threadId ? "selected" : ""}>
              <button className="chat-thread-select" type="button" onClick={() => chooseThread(item.id)} aria-current={item.id === threadId ? "page" : undefined}>
                <span>{item.title || "New conversation"}</span>
              </button>
              <button className="chat-delete-button" type="button" aria-label={`Delete ${item.title || "conversation"}`} onClick={() => void removeThread(item.id)} disabled={sending}>
                <Trash2 aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>

        {accountRuns.length > 0 && (
          <div className="chat-sidebar-accounts">
            <span className="chat-sidebar-title">Recent Account Runs</span>
            <ul className="chat-accounts-list">
              {accountRuns.map((acc) => (
                <li key={acc.domain}>
                  <Link href={`/accounts/${acc.domain}`} className="chat-account-item" title={`View artifacts for ${acc.domain}`}>
                    <Building2 className="chat-acc-icon" aria-hidden="true" />
                    <span className="chat-acc-domain">{acc.name || acc.domain}</span>
                    <ChevronRight className="chat-acc-chevron" aria-hidden="true" />
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}

        {!canChat ? <p className="chat-sidebar-note">Your role can view workspace data but cannot start a chat.</p> : null}
        <ProviderKeyManager canManage={canManageKey} />
      </aside>

      <section className="chat-main" aria-labelledby="chat-title">
        <header className="chat-heading">
          <div>
            <p className="eyebrow">WORKSPACE ASSISTANT</p>
            <h1 id="chat-title">How can I help?</h1>
          </div>
          <div className="chat-heading-right">
            {threadId ? <span className="chat-thread-count">{messages.filter((item) => item.role === "user").length} messages</span> : null}
          </div>
        </header>

        <div className="chat-message-area" aria-label="Conversation">
          {messages.length === 0 ? (
            <div className="chat-welcome">
              <div className="chat-welcome-mark" aria-hidden="true">✳</div>
              <h2>Start a conversation</h2>
              <p>Ask a question or explore account insights. Claude Fable 5.1 analyzes stored research data automatically.</p>
            </div>
          ) : (
            <ol className="chat-messages">
              {messages.map((message) => (
                <li className={`chat-message ${message.role}`} key={message.id}>
                  <div className="chat-message-label">{message.role === "user" ? "You" : "Claude"}</div>
                  {message.role === "assistant" ? (
                    <div className="chat-markdown">
                      {message.content ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown> : <span className="chat-thinking" aria-label="Thinking"><i /><i /><i /></span>}
                    </div>
                  ) : <p>{message.content}</p>}
                </li>
              ))}
            </ol>
          )}
        </div>

        <div className="chat-compose-wrap">
          {error ? <p className="chat-error" role="alert">{error}</p> : null}
          <form className="chat-composer" onSubmit={(event) => { event.preventDefault(); void send(); }}>
            <div className="chat-composer-toolbar">
              <div className="chat-model-info" title="Models are routed automatically by the backend system">
                <span className="chat-model-pill">Claude Fable 5.1 · Automatic Routing</span>
                <span className="chat-model-pill" title="Project scope strictly enforced: off-topic queries outside ABM and account intelligence are rejected">Project Scope: Enforced</span>
                <span className="chat-model-pill" title="Dynamic per-user session salting: identical commands receive fresh, independent output per user">Fresh Output: Guaranteed</span>
              </div>
              {pipelineStage !== "idle" && (
                <div className={`chat-pipeline-status ${pipelineStage}`}>
                  {pipelineStage === "collecting" && <Loader2 className="chat-spin" aria-hidden="true" />}
                  {pipelineStage === "analyzing" && <Loader2 className="chat-spin" aria-hidden="true" />}
                  {pipelineStage === "checking" && <Loader2 className="chat-spin" aria-hidden="true" />}
                  {pipelineStage === "done" && <CheckCircle2 className="chat-icon-done" aria-hidden="true" />}
                  <span>
                    {pipelineStage === "collecting" && "1. Collecting data"}
                    {pipelineStage === "analyzing" && "2. Analyzing and predicting"}
                    {pipelineStage === "checking" && "3. Running checks"}
                    {pipelineStage === "done" && "4. Done"}
                    {pipelineStage === "review" && "Held for review"}
                  </span>
                  {runningCost && <span className="chat-cost-badge">Est. ${Number(runningCost).toFixed(4)}</span>}
                </div>
              )}
              <span className="chat-model-hint">Streaming on</span>
            </div>
            <label className="sr-only" htmlFor="chat-prompt">Message Claude</label>
            <textarea
              id="chat-prompt"
              ref={textarea}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              placeholder={canChat ? "Ask about target accounts, predictions, or follow-up insights…" : "Chat is not available for your role"}
              rows={1}
              maxLength={60000}
              disabled={!canChat || sending || loading}
            />
            <div className="chat-composer-footer">
              <span>{notice || "Enter to send · Shift + Enter for a new line"}</span>
              <button className="chat-send-button" type="submit" aria-label="Send message" disabled={!canChat || sending || !draft.trim()}>
                <ArrowUp aria-hidden="true" />
              </button>
            </div>
          </form>
          <p className="chat-disclaimer">Review important information before acting on it. Models are routed automatically.</p>
        </div>
      </section>
    </div>
  );
}

export default function ChatPage() {
  return <SignedIn>{(session) => (
    <ChatWorkspace canChat={session.role !== "viewer"} canManageKey={session.role === "admin"} />
  )}</SignedIn>;
}
