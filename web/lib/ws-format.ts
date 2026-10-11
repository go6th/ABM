// Small, pure formatters for the workspace screens. Dates read as people say them.
import { m } from "@/lib/messages";

const MONTH = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", timeZone: "UTC" });
const MONTH_YEAR = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

/** "6 October", with the year only when it is not the current one. */
export function day(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.getUTCFullYear() === now.getUTCFullYear() ? MONTH.format(d) : MONTH_YEAR.format(d);
}

/** "today", "yesterday", "3 days ago", then a date. */
export function ago(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const days = Math.floor((Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) -
    Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())) / 86_400_000);
  if (days <= 0) return m.when.today;
  if (days === 1) return m.when.yesterday;
  if (days < 7) return m.when.daysAgo(days);
  return day(iso, now);
}

/** "leadership_change" becomes "Leadership change". */
export function humanize(key: string): string {
  const text = key.replace(/[_-]+/g, " ").trim().toLowerCase();
  return text ? text[0]!.toUpperCase() + text.slice(1) : "";
}

export type RunState = "none" | "running" | "done" | "failed";

/** The plain state of a run status string from the backend. */
export function runState(status: string | null | undefined): RunState {
  if (!status) return "none";
  const s = status.toLowerCase();
  if (["succeeded", "completed", "complete", "done", "delivered", "partial"].includes(s)) return "done";
  if (["failed", "cancelled", "canceled", "rejected", "expired", "error", "budget_exceeded"].includes(s)) return "failed";
  return "running";
}
