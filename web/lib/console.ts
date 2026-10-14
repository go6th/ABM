// Pure formatters for the admin console screens (A1 to A7).
import type { ConsoleIsolation } from "@/lib/gen/api.gen";
import type { PillTone } from "@/ui/design";
import { ago } from "@/lib/ws-format";

const NUM = new Intl.NumberFormat("en-US");
const TIME = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });

/** 1,284 */
export function num(n: number): string {
  return NUM.format(n);
}

/** "Today", "Yesterday", "2 days ago", then a date; "Never" when there is none. */
export function lastActive(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "Never";
  const text = ago(iso, now);
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** "Today 06:00" for today, otherwise as lastActive. */
export function started(iso: string, now = new Date()): string {
  const text = lastActive(iso, now);
  return text === "Today" ? `Today ${TIME.format(new Date(iso))}` : text;
}

/** The isolation pill's words and tone. */
export function isolationPill(iso: ConsoleIsolation, now = new Date()): { text: string; tone: PillTone } {
  if (iso.status === "not_run") return { text: "Isolation check not run yet", tone: "neutral" };
  const when = iso.ran_at ? `${TIME.format(new Date(iso.ran_at))} ${lastActive(iso.ran_at, now).toLowerCase()}` : "";
  return iso.status === "passed"
    ? { text: `Isolation check passed · ${when}`, tone: "ok" }
    : { text: `Isolation check failed · ${when}`, tone: "bad" };
}

/** A client's status pill tone. */
export function clientTone(status: string): PillTone {
  if (status === "Active") return "ok";
  if (status === "Near credit limit" || status === "Expiring") return "warn";
  return "neutral";
}

/** A job status pill tone. */
export function runTone(status: string): PillTone {
  if (status === "Done") return "ok";
  if (status === "Running" || status === "Queued") return "run";
  if (status === "Failed") return "bad";
  if (status.startsWith("Waiting")) return "warn";
  return "neutral";
}

/** "Expires in 1 day" / "Expires today". */
export function expiresIn(days: number): string {
  if (days <= 0) return "Expires today";
  return `Expires in ${days} day${days === 1 ? "" : "s"}`;
}
