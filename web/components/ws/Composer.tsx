"use client";
// Composer: Enter sends, Shift+Enter adds a line, Stop while an answer streams.
import { useEffect, useRef } from "react";
import { ArrowUp, Square } from "lucide-react";
import { m } from "@/lib/messages";

export function Composer({
  value, onChange, onSend, onStop, streaming, placeholder, large = false, note = true, autoFocus = false,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop?: () => void;
  streaming: boolean;
  placeholder: string;
  large?: boolean;
  note?: boolean;
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
  return (
    <div>
      <form
        className={large ? "composer large" : "composer"}
        onSubmit={(e) => { e.preventDefault(); if (canSend) onSend(); }}
      >
        <label className="sr-only" htmlFor="ws-question">{m.composer.label}</label>
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
        {streaming && onStop ? (
          <button type="button" className="send stop" aria-label={m.composer.stop} onClick={onStop}>
            <Square size={14} strokeWidth={2} fill="currentColor" aria-hidden="true" />
          </button>
        ) : (
          <button type="submit" className="send" aria-label={m.composer.send} disabled={!canSend}>
            <ArrowUp size={18} strokeWidth={1.8} aria-hidden="true" />
          </button>
        )}
      </form>
      {note ? <p className="composer-note">{m.composer.note}</p> : null}
    </div>
  );
}
