"use client";
// Prose with numbered citation markers ("[2]") turned into CitationChips. Markers with no
// matching evidence stay as plain text. Text is rendered as React text nodes, never as HTML.
import { Fragment } from "react";
import { CitationChip } from "./CitationChip";
import type { Evidence } from "./evidence";

export function CitedText({
  text, evidence, pressed, onOpen, settle = false,
}: { text: string; evidence: Evidence[]; pressed: number | null; onOpen: (n: number, el: HTMLButtonElement) => void; settle?: boolean }) {
  const byN = new Map(evidence.map((e) => [e.n, e]));
  const parts = text.split(/(\[\d{1,3}\])/);
  const chipAt = (i: number) => {
    const hit = /^\[(\d{1,3})\]$/.exec(parts[i] ?? "");
    return hit ? byN.get(Number(hit[1])) : undefined;
  };
  return (
    <>
      {parts.map((part, i) => {
        const ev = chipAt(i);
        if (ev) return null;
        const next = chipAt(i + 1);
        if (!next) return <Fragment key={i}>{part}</Fragment>;
        // Keep a chip on the same line as the word it cites.
        const cut = part.search(/\S+\s*$/);
        const head = cut > 0 ? part.slice(0, cut) : cut === 0 ? "" : part;
        const tail = cut >= 0 ? part.slice(cut).trimEnd() : "";
        return (
          <Fragment key={i}>
            {head}
            <span className="ws-nowrap">
              {tail}
              <CitationChip n={next.n} grade={next.grade} pressed={pressed === next.n} onOpen={onOpen} settle={settle} />
            </span>
          </Fragment>
        );
      })}
    </>
  );
}
