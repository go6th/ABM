"use client";
// Refresh from web as designed, plus research on one topic the user types. Three scopes and the
// topic are radio cards (arrow keys move between them); typing in the topic box selects it. Then
// the credits left this month, Confirm refresh and Stay local. Nothing leaves the workspace until
// Confirm; Confirm sends the credits the user saw, so a moved estimate is shown again instead of
// being charged.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { RefreshOptions, RefreshStarted } from "@/lib/gen/api.gen";

type Option = RefreshOptions["options"][number];
type Scope = Option["scope"] | "topic";

const TOPIC_MAX = 300;

function minutes(m: number): string {
  if (m >= 240) return "About a working day";
  if (m >= 60) return `About ${Math.round(m / 60)} hours`;
  return `About ${m} min`;
}

function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

export function RefreshForm({ view, initialTopic }: { view: RefreshOptions; initialTopic: string }) {
  const router = useRouter();
  const [options, setOptions] = useState<Option[]>(view.options);
  const [topicCost, setTopicCost] = useState(view.topic);
  const [topic, setTopic] = useState(initialTopic.slice(0, TOPIC_MAX));
  const start: Scope = initialTopic
    ? "topic"
    : (options.find((o) => o.scope === "people" && o.affordable) ?? options.find((o) => o.affordable) ?? options[0]!).scope;
  const [scope, setScope] = useState<Scope>(start);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const requestId = useRef(crypto.randomUUID());
  const group = useRef<HTMLDivElement>(null);
  const topicInput = useRef<HTMLTextAreaElement>(null);
  const order: Scope[] = [...options.map((o) => o.scope), "topic"];
  const credits = scope === "topic" ? topicCost.credits : options.find((o) => o.scope === scope)!.credits;
  const left = view.credits.left;
  const short = credits > left;
  const topicText = topic.trim().replace(/\s+/g, " ");
  const needsTopic = scope === "topic" && topicText.length < 3;
  const back = `/accounts/${encodeURIComponent(view.domain)}`;

  const focusScope = (next: Scope) => {
    setScope(next);
    group.current?.querySelector<HTMLButtonElement>(`[data-scope="${next}"]`)?.focus();
  };
  const move = (delta: number) => focusScope(order[(order.indexOf(scope) + delta + order.length) % order.length]!);

  const confirm = async () => {
    if (busy || short || needsTopic) return;
    setBusy(true);
    setMessage(null);
    try {
      await api<RefreshStarted>("POST", `/v1/accounts/${encodeURIComponent(view.domain)}/refresh`, {
        request_id: requestId.current,
        scope,
        credits,
        ...(scope === "topic" ? { topic: topicText } : {}),
      });
      router.push(back);
      router.refresh();
    } catch (cause) {
      requestId.current = crypto.randomUUID();
      if (cause instanceof ApiError && cause.type === "estimate_changed" && typeof cause.context?.credits === "number") {
        const moved = cause.context.credits as number;
        if (scope === "topic") setTopicCost((t) => ({ ...t, credits: moved, affordable: moved <= left }));
        else setOptions((all) => all.map((o) => (o.scope === scope ? { ...o, credits: moved, affordable: moved <= left } : o)));
        setMessage(cause.detail);
      } else if (cause instanceof ApiError && (cause.status === 409 || cause.status === 422)) {
        setMessage(cause.status === 422 ? "Describe the topic in 3 to 300 characters." : cause.detail);
      } else if (cause instanceof ApiError && cause.status === 403) {
        setMessage("Web refreshes are turned off for your workspace.");
      } else {
        setMessage("The refresh could not be started. Try again in a moment.");
      }
      setBusy(false);
    }
  };

  const topicOn = scope === "topic";
  return (
    <>
      <div
        ref={group}
        className="opts"
        role="radiogroup"
        aria-label="What to refresh"
        onKeyDown={(e) => {
          if ((e.target as HTMLElement).tagName === "TEXTAREA") return;
          if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); move(1); }
          if (e.key === "ArrowUp" || e.key === "ArrowLeft") { e.preventDefault(); move(-1); }
        }}
      >
        {options.map((o) => {
          const on = o.scope === scope;
          return (
            <button
              key={o.scope}
              type="button"
              className="opt"
              role="radio"
              aria-checked={on}
              tabIndex={on ? 0 : -1}
              data-scope={o.scope}
              onClick={() => setScope(o.scope)}
            >
              <span className="in">
                <span className="radio-dot" aria-hidden="true" />
                <span className="grow">
                  <span className="opt-name">{o.name}</span>
                  <span className="opt-desc mut">{o.description}</span>
                </span>
                <span className="opt-cost">
                  <span className="strong">{minutes(o.minutes)}</span>
                  <span className="mut">~{fmt(o.credits)} credits</span>
                </span>
              </span>
            </button>
          );
        })}

        <div className={topicOn ? "opt topic-card on" : "opt topic-card"}>
          <button
            type="button"
            className="topic-pick"
            role="radio"
            aria-checked={topicOn}
            tabIndex={topicOn ? 0 : -1}
            data-scope="topic"
            onClick={() => { setScope("topic"); topicInput.current?.focus(); }}
          >
            <span className="in">
              <span className={topicOn ? "radio-dot on" : "radio-dot"} aria-hidden="true" />
              <span className="grow">
                <span className="opt-name">A specific topic</span>
                <span className="opt-desc mut">Type what you want researched about {view.name}</span>
              </span>
              <span className="opt-cost">
                <span className="strong">{minutes(topicCost.minutes)}</span>
                <span className="mut">~{fmt(topicCost.credits)} credits</span>
              </span>
            </span>
          </button>
          <div className="topic-box">
            <label className="sr-only" htmlFor="refresh-topic">Topic to research about {view.name}</label>
            <textarea
              id="refresh-topic"
              ref={topicInput}
              rows={2}
              maxLength={TOPIC_MAX}
              value={topic}
              placeholder={`For example: ${view.name}'s plans for its customer data platform this year`}
              onFocus={() => setScope("topic")}
              onChange={(e) => { setTopic(e.target.value); setScope("topic"); }}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void confirm(); } }}
              aria-describedby="topic-hint"
            />
            <div className="topic-hint mut" id="topic-hint">
              <span>{topicOn && needsTopic ? "Describe the topic in a few words." : "Only this topic is researched. Enter confirms."}</span>
              <span className="count">{topic.length}/{TOPIC_MAX}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="card pad-sm kv">
        <span className="mut">Research credits left this month</span>
        <span>{fmt(left)} of {fmt(view.credits.pool)}</span>
      </div>

      {short ? (
        <div className="notice warn" role="status">
          <p>This needs about {fmt(credits)} credits and {fmt(left)} are left this month. Choose a smaller refresh, or wait until the pool resets on the 1st.</p>
        </div>
      ) : null}
      {message ? <div className="notice error" role="alert"><p>{message}</p></div> : null}

      <div className="actions">
        <button type="button" className="btn primary wide" onClick={() => void confirm()} disabled={busy || short || needsTopic} aria-describedby="refresh-note">
          {busy ? "Starting refresh" : topicOn ? "Confirm research" : "Confirm refresh"}
        </button>
        <Link className="btn" href={back} prefetch={false}>Stay local</Link>
        <span className="mut small" id="refresh-note">You will be notified when it finishes.</span>
      </div>
    </>
  );
}
