"use client";
// Composer: the text box, a Normal | Deep segmented control, Add file, the scope selector and
// Send. Enter sends, Shift+Enter adds a line, Stop while an answer streams.
import Link from "next/link";
import { useEffect, useRef } from "react";
import { m } from "@/lib/messages";
import { cellText } from "@/lib/render";

export type Mode = "normal" | "deep";
export interface ScopeOption { domain: string; name: string }

export function Composer({
  value, onChange, onSend, onStop, streaming, placeholder, mode, onMode, scope, onScope, accounts, autoFocus = false,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop?: () => void;
  streaming: boolean;
  placeholder: string;
  mode: Mode;
  onMode: (mode: Mode) => void;
  scope: string | null;
  onScope: (domain: string | null) => void;
  accounts: ScopeOption[];
  autoFocus?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);
  const canSend = value.trim().length > 0 && !streaming;
  const fileHref = scope ? `/research/new?mode=file&domain=${encodeURIComponent(scope)}` : "/research/new?mode=file";
  return (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); if (canSend) onSend(); }}>
      <label className="sr-only" htmlFor="ws-question">{m.ask.placeholder}</label>
      <textarea
        id="ws-question"
        ref={ref}
        rows={1}
        value={value}
        placeholder={placeholder}
        autoFocus={autoFocus}
        maxLength={60000}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) onSend();
          }
        }}
      />
      <div className="bar">
        <div className="seg" role="group" aria-label={m.ask.modeLabel}>
          <button type="button" aria-pressed={mode === "normal"} onClick={() => onMode("normal")}>{m.ask.normal}</button>
          <button type="button" aria-pressed={mode === "deep"} onClick={() => onMode("deep")}>{m.ask.deep}</button>
        </div>
        <Link className="link-btn small" href={fileHref}>{m.ask.addFile}</Link>
        <label className="sr-only" htmlFor="ws-scope">{m.ask.scopeLabel}</label>
        <select id="ws-scope" className="select" value={scope ?? ""} onChange={(e) => onScope(e.target.value || null)}>
          <option value="">{m.ask.allAccounts}</option>
          {accounts.map((a) => <option key={a.domain} value={a.domain}>{cellText(a.name, 60)}</option>)}
        </select>
        <span className="grow" />
        {streaming && onStop ? (
          <button type="button" className="btn" onClick={onStop}>{m.ask.stop}</button>
        ) : (
          <button type="submit" className="btn primary" disabled={!canSend}>{m.ask.send}</button>
        )}
      </div>
    </form>
  );
}
