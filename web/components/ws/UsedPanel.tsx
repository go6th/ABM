"use client";
// UsedPanel ("What this answer used"): a one-line summary, expandable to three rows.
import { ChevronRight } from "lucide-react";
import { m } from "@/lib/messages";

export interface Used {
  facts: number;
  updated: string;
  web: string | null;
  mail: string | null;
}

export function UsedPanel({ used }: { used: Used }) {
  return (
    <details className="ws-used">
      <summary>
        <ChevronRight size={14} strokeWidth={1.6} aria-hidden="true" />
        <span className="ws-sr">{m.used.title}: </span>
        {m.used.summary(used.facts, used.web !== null)}
      </summary>
      <dl>
        <dt>{m.used.held}</dt><dd>{m.used.heldValue(used.facts, used.updated)}</dd>
        <dt>{m.used.web}</dt><dd className={used.web ? "web-used" : undefined}>{used.web ?? m.used.webNot}</dd>
        <dt>{m.used.mail}</dt><dd>{used.mail ?? m.used.mailNot}</dd>
      </dl>
    </details>
  );
}
