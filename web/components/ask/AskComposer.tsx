"use client";
// The composer: the question field (Enter sends), then the Accounts chip (all accounts or one),
// the model chip (the conversation's analysis model, with its effort chosen inside the same menu)
// and Ask. Answers come from the workspace. Owner request 2026-11-08: the "+" menu, the Search
// toggle and the separate Effort chip were removed; files are added on an account's Files tab, and
// checking the web goes through an account's Refresh screen (the answer's "Refresh from web"
// offer opens the hint below, which links there once an account is picked).
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { refreshHref } from "./AnswerView";
import { ModelMenu } from "@/components/ModelMenu";
import type { ModelChoices } from "@/lib/gen/api.gen";

export interface ScopeAccount { id: string; domain: string; name: string }

export function AskComposer({
  variant, draft, onDraft, onSend, onStop, busy, accounts, scope, onScope, placeholder, menuDown = false, webHintOpen, onWebHint,
  models = null, model = null, onModel, effort = null, onEffort, meter = null,
}: {
  variant: "hero" | "follow" | "account";
  draft: string;
  onDraft: (value: string) => void;
  onSend: () => void;
  /** ends the answer that is streaming; with it, Ask turns into Stop while busy */
  onStop?: () => void;
  busy: boolean;
  accounts: ScopeAccount[];
  scope: ScopeAccount | null;
  onScope?: (value: ScopeAccount | null) => void;
  placeholder: string;
  menuDown?: boolean;
  webHintOpen: boolean;
  onWebHint: (open: boolean) => void;
  models?: ModelChoices | null;
  model?: string | null;
  onModel?: (id: string) => void;
  effort?: string | null;
  onEffort?: (effort: string) => void;
  /** the token meter, shown beside Ask once the conversation has an answer */
  meter?: React.ReactNode;
}) {
  const inputId = useId();
  const menuId = useId();
  const [menuOpen, setMenuOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const scopeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const first = wrap.current?.querySelector<HTMLElement>('[role="menuitemradio"][aria-checked="true"]')
      ?? wrap.current?.querySelector<HTMLElement>('[role="menuitemradio"]');
    first?.focus();
    const onDown = (event: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setMenuOpen(false); scopeButton.current?.focus(); }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        const items = Array.from(wrap.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? []);
        const i = items.indexOf(document.activeElement as HTMLElement);
        const next = items[(i + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length];
        next?.focus();
        event.preventDefault();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [menuOpen]);

  // picking an account keeps the web hint open: it then links to that account's Refresh screen
  const pick = (value: ScopeAccount | null) => {
    onScope?.(value);
    setMenuOpen(false);
    scopeButton.current?.focus();
  };

  const send = () => {
    if (!draft.trim()) { document.getElementById(inputId)?.focus(); return; }
    onSend();
  };

  const chip = variant === "account" ? "chipa quiet" : "chipa";
  const scopeLabel = scope ? scope.name : "Accounts";
  const label = variant === "follow" ? "Ask a follow up" : variant === "account" && scope ? `Ask about ${scope.name}` : "Ask a question";

  return (
    <div className={`composer ${variant === "hero" ? "" : variant}`}>
      <label htmlFor={inputId} className="sr-only">{label}</label>
      <input
        id={inputId}
        value={draft}
        placeholder={placeholder}
        autoComplete="off"
        maxLength={4000}
        onChange={(e) => onDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
      />
      <div className="bar">
        {onScope ? (
          <div className="menu-wrap" ref={wrap}>
            <button
              ref={scopeButton}
              type="button"
              className="chipa"
              aria-haspopup="menu"
              aria-label={scope ? `Answer from ${scope.name}. Change accounts` : "Answer from all accounts. Change accounts"}
              aria-expanded={menuOpen}
              aria-controls={menuOpen ? menuId : undefined}
              onClick={() => setMenuOpen((v) => !v)}
            >
              {scopeLabel}
            </button>
            {menuOpen ? (
              <div className={`menu ${menuDown ? "down" : ""}`} role="menu" id={menuId} aria-label="Answer from">
                <button type="button" role="menuitemradio" aria-checked={scope === null} onClick={() => pick(null)}>All accounts</button>
                {accounts.map((a) => (
                  <button key={a.id} type="button" role="menuitemradio" aria-checked={scope?.id === a.id} onClick={() => pick(a)}>{a.name}</button>
                ))}
              </div>
            ) : null}
          </div>
        ) : (
          <span className={chip}>{scopeLabel}</span>
        )}
        {models && model && onModel ? (
          <ModelMenu choices={models} value={model} onChange={onModel} menuDown={menuDown} className={chip} effort={effort} onEffort={onEffort} />
        ) : null}
        <span className="spacer" />
        {meter}
        {busy && onStop ? (
          <button type="button" className="btn" onClick={onStop}>Stop</button>
        ) : (
          <button
            type="button"
            className="btn primary go"
            disabled={busy || !draft.trim()}
            onClick={send}
          >
            Ask
          </button>
        )}
      </div>
      {webHintOpen ? (
        <p className="hint" id={`${inputId}-web`} role="status">
          {scope ? (
            <>
              <Link href={refreshHref(scope.domain)} prefetch={false}>Refresh {scope.name} from the web</Link>. You confirm it first, and it uses one of your researches.
            </>
          ) : "To check the web, pick one account under Accounts. You confirm it first, and it uses one of your researches."}
          {" "}<button type="button" className="link-btn small" onClick={() => onWebHint(false)}>Hide</button>
        </p>
      ) : null}
    </div>
  );
}
