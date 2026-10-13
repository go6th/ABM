// GradeMark: the grade as a colour and always its label.
import { m } from "@/lib/messages";

export type Grade = "confirmed" | "probable" | "hypothesis" | "lead" | "internal";

/** The product grade of a backend value, or null for anything that is not one of them. */
export function toGrade(value: unknown): Grade | null {
  if (typeof value !== "string") return null;
  const key = value.trim().toLowerCase();
  return key === "confirmed" || key === "probable" || key === "hypothesis" || key === "lead" || key === "internal" ? key : null;
}

export function GradeMark({ grade }: { grade: Grade }) {
  return <span className={`grade ${grade}`}>{m.grade[grade]}</span>;
}
