// The Accounts list: the news line under each name and the order of the rows. Pure.
import type { AccountPage } from "@/lib/gen/api.gen";

export type AccountItem = AccountPage["accounts"][number];

export function subline(a: AccountItem): { text: string; tone: string } {
  switch (a.state) {
    case "new_signals": return { text: `${a.new_signals} new signal${a.new_signals === 1 ? "" : "s"}`, tone: "ok" };
    case "first_scan": return { text: "First scan complete", tone: "" };
    case "no_change": return { text: "No change", tone: "" };
    case "researching": return { text: "Researching", tone: "run" };
    case "waiting_credit_limit": return { text: "Waiting to start", tone: "neutral" };
    case "failed": return { text: "Last refresh did not finish", tone: "warn" };
    default: return { text: "Not researched yet", tone: "" };
  }
}

/** Most news first, then most recently researched; on the same day an account that has been
 * refreshed before one with only a first scan, then by name. */
export function orderAccounts(accounts: readonly AccountItem[]): AccountItem[] {
  const watched = (a: AccountItem) => (a.state === "new_signals" || a.state === "no_change" ? 0 : 1);
  return [...accounts].sort((x, y) =>
    (y.new_signals ?? 0) - (x.new_signals ?? 0)
    || (y.last_researched_at ?? "").slice(0, 10).localeCompare((x.last_researched_at ?? "").slice(0, 10))
    || watched(x) - watched(y)
    || (x.name ?? x.domain).localeCompare(y.name ?? y.domain));
}
