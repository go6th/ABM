"use client";
// The token meter beside the Ask box, as claude.ai shows its usage: a small ring for how much of
// the model's context window the latest answer used, and a panel with the tokens and the cost of
// the whole conversation. Nothing is shown until the conversation has an answer.
import { useEffect, useId, useRef, useState } from "react";
import { api } from "@/lib/api";
import { contextTokens, readTokens, sumUsage, type TurnUsage } from "@/lib/chat-usage";
import type { ChatModel, ChatModels } from "@/lib/gen/api.gen";
import { dollars, tokenCount } from "@/lib/usage";

const RADIUS = 7.5;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function ContextMeter({ usages, modelId }: {
  /** the answers of the conversation, oldest first */
  usages: TurnUsage[];
  /** the model to measure against when the latest answer has none (a workspace answer) */
  modelId: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<ChatModel[] | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const panelId = useId();

  // the context windows: loaded once, the meter shows tokens alone until they arrive
  useEffect(() => {
    let live = true;
    api<ChatModels>("GET", "/v1/models")
      .then((m) => { if (live) setModels(m.models); })
      .catch(() => { if (live) setModels([]); });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => { if (wrap.current && !wrap.current.contains(event.target as Node)) setOpen(false); };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const latest = usages.at(-1);
  if (!latest) return null;

  const total = sumUsage(usages);
  // the window share is the last round's prompt; a turn that did not report it (stored before
  // 2026-11-04) shows the tokens it read, never a share computed from every round added up
  const fill = contextTokens(latest);
  const read = readTokens(latest);
  const used = fill ?? read;
  const id = latest.model ?? modelId;
  const model = models?.find((m) => m.id === id) ?? null;
  const size = model?.context_window ?? null;
  const share = size && fill !== null ? Math.min(1, fill / size) : null;
  const percent = share === null ? null : Math.round(share * 100);
  const label = percent === null ? `${tokenCount(used)} tokens` : `${percent}% of the context used`;
  const cached = total.cacheRead;

  return (
    <div className="menu-wrap" ref={wrap}>
      <button
        type="button"
        className="ctx-btn"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={`Token use: ${label}. Show details`}
        title="Token use"
        onClick={() => setOpen((v) => !v)}
      >
        <svg className="ctx-ring" viewBox="0 0 20 20" aria-hidden="true">
          <circle className="ctx-track" cx="10" cy="10" r={RADIUS} />
          <circle
            className="ctx-fill"
            cx="10"
            cy="10"
            r={RADIUS}
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={CIRCUMFERENCE * (1 - (share ?? 0))}
          />
        </svg>
        <span className="ctx-label">{percent === null ? tokenCount(used) : `${percent}%`}</span>
      </button>
      {open ? (
        <div className="usage-pop" role="dialog" id={panelId} aria-label="Token use">
          <p className="usage-title">Context</p>
          <p className="usage-big">
            {tokenCount(used)}{size ? ` of ${tokenCount(size)}` : ""} tokens
            {percent === null ? null : <span className="mut"> · {percent}%</span>}
          </p>
          <dl className="usage-rows">
            <div><dt>Latest answer</dt><dd>{tokenCount(read)} read · {tokenCount(latest.output)} out</dd></div>
            <div><dt>This conversation</dt><dd>{tokenCount(total.input + cached + total.cacheWrite)} in · {tokenCount(total.output)} out</dd></div>
            <div><dt>Served from cache</dt><dd>{tokenCount(cached)} tokens</dd></div>
            <div><dt>Cost so far</dt><dd>{dollars(total.costUsd)}</dd></div>
            <div><dt>Model</dt><dd>{model?.display_name ?? (latest.model && latest.model !== "workspace-local" ? latest.model : "Workspace answers")}</dd></div>
          </dl>
          <p className="mut xs-text">
            The context is the prompt of the latest answer&apos;s last step, cached input included; its window is the most the model can read at once. An answer that looks things up reads in several steps, so the tokens it read can be more than its context.
          </p>
        </div>
      ) : null}
    </div>
  );
}
