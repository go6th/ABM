"use client";
// CitationChip: an inline, numbered source marker. Numbers follow order of appearance.
import { GradeMark, type Grade } from "./GradeMark";
import { m } from "@/lib/messages";

export function CitationChip({
  n, grade, pressed, onOpen, settle = false,
}: { n: number; grade: Grade; pressed: boolean; onOpen: (n: number, el: HTMLButtonElement) => void; settle?: boolean }) {
  return (
    <button
      type="button"
      className={settle ? "cite settle" : "cite"}
      aria-pressed={pressed}
      aria-label={`${m.evidence.source(n)}, ${m.grade[grade]}`}
      onClick={(e) => onOpen(n, e.currentTarget)}
    >
      <GradeMark grade={grade} showLabel={false} />
      <span aria-hidden="true">{n}</span>
    </button>
  );
}
