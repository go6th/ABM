"use client";
// Ask: one conversation. Normal answers come from what the workspace holds, streamed from the
// chat endpoint, with fact citations as numbered inline links. Deep starts a research run for the
// chosen account, shows the steps (and the checkpoint card when it pauses), then answers the
// question from the refreshed workspace. Question text goes only to the API.
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, apiStream, ApiError, newIdempotencyKey } from "@/lib/api";
import { m } from "@/lib/messages";
import { cellText } from "@/lib/render";
import type { AccountAdded, RunCreated } from "@/lib/gen/api.gen";
import { Answer, cannotAnswer, readFrames } from "./Conversation";
import { Composer, type Mode, type ScopeOption } from "./Composer";
import { RunPanel, useRun } from "./RunSteps";
import { EmptyState } from "@/ui/primitives";

export interface Turn {
  id: string;
  question: string;
  answer: string;
  status: "streaming" | "done" | "stopped" | "failed" | "run";
  scope: string | null;
  runId?: string;
}

function failure(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 429) return m.ask.limit;
    if (cause.status === 503) return m.ask.unavailable;
    if (cause.type === "out_of_context" || cause.status === 422) return m.ask.outOfScope;
  }
  return m.ask.failed;
}

function DeepTurn({ runId, onFinished }: { runId: string; onFinished: () => void }) {
  const { terminal, status } = useRun(runId);
  const fired = useRef(false);
  useEffect(() => {
    if (terminal && !fired.current && (status === "COMPLETED" || status === "PARTIAL")) { fired.current = true; onFinished(); }
  }, [terminal, status, onFinished]);
  return <RunPanel runId={runId} compact />;
}

export function Ask({ accounts, threadId: initialThread = null, initialTurns = [], initialScope = null }: {
  accounts: ScopeOption[];
  threadId?: string | null;
  initialTurns?: Turn[];
  initialScope?: string | null;
}) {
  const router = useRouter();
  const [threadId, setThreadId] = useState<string | null>(initialThread);
  const [turns, setTurns] = useState<Turn[]>(initialTurns);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<Mode>("normal");
  const [scope, setScope] = useState<string | null>(initialScope);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const accountIds = useRef(new Map<string, string>());
  const streaming = turns.some((t) => t.status === "streaming");
  const scoped = accounts.find((a) => a.domain === scope) ?? null;

  useEffect(() => () => abort.current?.abort(), []);

  const patch = (id: string, change: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...(typeof change === "function" ? change(t) : change) } : t)));

  const accountId = async (domain: string | null): Promise<string | null> => {
    if (!domain) return null;
    const known = accountIds.current.get(domain);
    if (known) return known;
    const added = await api<AccountAdded>("POST", "/v1/accounts", { domain }, { "Idempotency-Key": newIdempotencyKey() });
    accountIds.current.set(domain, added.account_id);
    return added.account_id;
  };

  const askNormal = useCallback(async (question: string, domain: string | null) => {
    const text = question.trim();
    if (!text || streaming) return;
    setError(null);
    setDraft("");
    const id = crypto.randomUUID();
    setTurns((all) => [...all, { id, question: text, answer: "", status: "streaming", scope: domain }]);
    const controller = new AbortController();
    abort.current = controller;
    try {
      let active = threadId;
      if (!active) {
        const created = await api<{ id: string }>("POST", "/v1/chat/threads", {});
        active = created.id;
        setThreadId(active);
        window.history.replaceState(null, "", `/?thread=${encodeURIComponent(active)}`);
      }
      const account = await accountId(domain);
      const reply = await apiStream(`/v1/chat/threads/${encodeURIComponent(active)}/turns/stream`, {
        request_id: id, content: text, enforce_project_scope: true, fresh_output: true, account_id: account,
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
      if (controller.signal.aborted) { patch(id, { status: "stopped" }); return; }
      setTurns((all) => all.filter((t) => t.id !== id || t.answer.length > 0).map((t) => (t.id === id ? { ...t, status: "failed" } : t)));
      setError(failure(cause));
      setDraft(text);
    } finally {
      abort.current = null;
      router.refresh();
    }
  }, [streaming, threadId, router]);

  const askDeep = useCallback(async (question: string, domain: string | null) => {
    const text = question.trim();
    if (!text || streaming) return;
    if (!domain) { setError(m.ask.deepNeedsAccount); return; }
    setError(null);
    setDraft("");
    const id = crypto.randomUUID();
    try {
      const created = await api<RunCreated>("POST", "/v1/runs", { domain, depth: "deep" }, { "Idempotency-Key": newIdempotencyKey() });
      setTurns((all) => [...all, { id, question: text, answer: "", status: "run", scope: domain, runId: created.run_id }]);
    } catch (cause) {
      setError(cause instanceof ApiError && cause.status === 409 ? m.ask.deepBusy : failure(cause));
      setDraft(text);
    }
  }, [streaming]);

  const ask = (question: string) => (mode === "deep" ? void askDeep(question, scope) : void askNormal(question, scope));

  const sourcesHref = (domain: string | null) => (ref: string) =>
    domain ? `/accounts/${encodeURIComponent(domain)}/sources?ref=${encodeURIComponent(ref)}` : `/accounts?ref=${encodeURIComponent(ref)}`;

  return (
    <div className="page narrow chat">
      {turns.length === 0 ? (
        accounts.length === 0 ? (
          <EmptyState title={m.ask.noAccountsTitle} body={m.ask.noAccountsBody} action={{ href: "/research/new", label: m.ask.startResearch }} />
        ) : (
          <div className="stack">
            <h1>{m.ask.title}</h1>
            <ul className="starters" aria-label="Starter questions">
              {m.ask.starters.map((q) => <li key={q}><button type="button" className="link-btn" onClick={() => ask(q)}>{q}</button></li>)}
            </ul>
          </div>
        )
      ) : null}
      {turns.length > 0 ? (
        <div className="thread">
          {turns.map((t) => (
            <article key={t.id} className="turn" aria-busy={t.status === "streaming"}>
              <p className="q"><span className="sr-only">{m.ask.you}: </span>{t.question}</p>
              {t.status === "run" && t.runId ? (
                <div className="stack">
                  <p className="small muted">{m.ask.deepStarted(cellText(accounts.find((a) => a.domain === t.scope)?.name ?? t.scope ?? ""))}</p>
                  <DeepTurn runId={t.runId} onFinished={() => void askNormal(t.question, t.scope)} />
                </div>
              ) : null}
              <div aria-live="polite">
                {t.answer || t.status === "streaming" ? <Answer text={t.answer} streaming={t.status === "streaming"} sourcesHref={sourcesHref(t.scope)} /> : null}
              </div>
              {t.status === "streaming" && !t.answer ? <p className="loading small"><span className="spinner" aria-hidden="true" />{m.ask.answering}</p> : null}
              {t.status === "stopped" ? <p className="small muted">{m.ask.stopped}</p> : null}
              {t.status === "done" && cannotAnswer(t.answer) ? (
                <div className="actions">
                  <button type="button" className="btn primary" onClick={() => { setMode("deep"); void askDeep(t.question, t.scope ?? scope); }}>{m.ask.runDeep}</button>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      ) : null}
      <div className="dock">
        {error ? <p className="error-text small" role="alert">{error}</p> : null}
        <Composer
          value={draft}
          onChange={setDraft}
          onSend={() => ask(draft)}
          onStop={() => abort.current?.abort()}
          streaming={streaming}
          placeholder={scoped ? m.ask.placeholderAccount(cellText(scoped.name, 60)) : m.ask.placeholder}
          mode={mode}
          onMode={setMode}
          scope={scope}
          onScope={setScope}
          accounts={accounts}
          autoFocus={turns.length === 0}
        />
        <p className="hint">{m.ask.normalHint}</p>
      </div>
    </div>
  );
}
