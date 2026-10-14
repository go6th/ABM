"use client";
// The composer as designed: the question field (Enter sends), then "+ Upload a file", the scope
// chip (all accounts or one), the "Web: off" chip and Ask. Answers never use the web; pressing
// Web opens the Refresh confirmation for the scoped account, or asks for an account first.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { refreshHref } from "./AnswerView";

export interface ScopeAccount { id: string; domain: string; name: string }

export function AskComposer({
  variant, draft, onDraft, onSend, busy, accounts, scope, onScope, placeholder, menuDown = false, webHintOpen, onWebHint,
}: {
  variant: "hero" | "follow" | "account";
  draft: string;
  onDraft: (value: string) => void;
  onSend: () => void;
  busy: boolean;
  accounts: ScopeAccount[];
  scope: ScopeAccount | null;
  onScope?: (value: ScopeAccount | null) => void;
  placeholder: string;
  menuDown?: boolean;
  webHintOpen: boolean;
  onWebHint: (open: boolean) => void;
}) {
  const router = useRouter();
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

  const pick = (value: ScopeAccount | null) => {
    onScope?.(value);
    setMenuOpen(false);
    onWebHint(false);
    scopeButton.current?.focus();
  };

  const web = () => {
    if (scope) { router.push(refreshHref(scope.domain)); return; }
    onWebHint(true);
    if (onScope) setMenuOpen(true);
  };

  const chip = variant === "account" ? "chipa quiet" : "chipa";
  const scopeLabel = scope ? `${scope.name} only` : `All ${accounts.length} account${accounts.length === 1 ? "" : "s"}`;
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
        onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); onSend(); } }}
      />
      <div className="bar">
        {variant !== "account" ? <Link className="chipa" href="/research/new?tab=file" prefetch={false}>+ Upload a file</Link> : null}
        {onScope ? (
          <div className="menu-wrap" ref={wrap}>
            <button
              ref={scopeButton}
              type="button"
              className="chipa"
              aria-haspopup="menu"
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
        <button type="button" className={chip} aria-pressed={false} aria-describedby={webHintOpen ? `${inputId}-web` : undefined} onClick={web}>
          Web: off
        </button>
        <span className="spacer" />
        <button
          type="button"
          className="btn primary"
          disabled={busy}
          onClick={() => { if (draft.trim()) onSend(); else document.getElementById(inputId)?.focus(); }}
        >
          Ask
        </button>
      </div>
      {webHintOpen ? (
        <p className="hint" id={`${inputId}-web`} role="status">
          Answers come from your workspace. To check the web, pick one account; you confirm the refresh and its credits first.
        </p>
      ) : null}
    </div>
  );
}
