// GradeMark: a 10px ring whose wedge fill shows how sure we are. The fill is never the only
// carrier of meaning: the plain label is rendered next to it, or given as the accessible name.
import { m } from "@/lib/messages";

export type Grade = "confirmed" | "probable" | "hypothesis" | "lead";

const FILL: Record<Grade, number> = { confirmed: 1, probable: 0.75, hypothesis: 0.5, lead: 0.25 };

/** The product grade of a backend value, or null for anything that is not one of the four. */
export function toGrade(value: unknown): Grade | null {
  if (typeof value !== "string") return null;
  const key = value.trim().toLowerCase();
  return key === "confirmed" || key === "probable" || key === "hypothesis" || key === "lead" ? key : null;
}

/** SVG path of a pie wedge from 12 o'clock, clockwise, for a fraction in (0, 1). */
export function wedgePath(fraction: number, r = 3.2, c = 5): string {
  const angle = 2 * Math.PI * fraction;
  const x = c + r * Math.sin(angle);
  const y = c - r * Math.cos(angle);
  const large = fraction > 0.5 ? 1 : 0;
  return `M${c} ${c}V${c - r}A${r} ${r} 0 ${large} 1 ${x.toFixed(3)} ${y.toFixed(3)}Z`;
}

export function GradeMark({ grade, showLabel = true }: { grade: Grade; showLabel?: boolean }) {
  const fill = FILL[grade];
  const label = m.grade[grade];
  return (
    <span className="grade" data-grade={grade} title={m.grade.help[grade]}>
      <svg viewBox="0 0 10 10" aria-hidden="true" focusable="false">
        <circle className="ring" cx="5" cy="5" r="4.3" />
        {fill >= 1 ? <circle className="wedge" cx="5" cy="5" r="3.2" /> : <path className="wedge" d={wedgePath(fill)} />}
      </svg>
      {showLabel ? <span>{label}</span> : <span className="sr-only">{label}</span>}
    </span>
  );
}
