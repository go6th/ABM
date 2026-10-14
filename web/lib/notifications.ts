// The sidebar's notes: which notifications show, what they say, and where they lead. Pure, so
// the wording and the link rules are tested apart from the layout that renders them.
import type { Notifications } from "@/lib/gen/api.gen";

export interface ShellNote { id: string; text: string; meta: string; href: string; warn: boolean }

type Notification = Notifications["notifications"][number];

const SCOPE: Record<string, string> = { jobs: "job postings", people: "people and teams", full: "full refresh", topic: "topic" };

const WARNING_KINDS = new Set(["research_failed", "research_stalled", "access_expiring", "research_limit_reached", "limit_request_declined"]);

function text(n: Notification): string {
  const name = n.account_name ?? "An account";
  const what = n.scope && SCOPE[n.scope] ? ` (${SCOPE[n.scope]})` : "";
  switch (n.kind) {
    case "access_expiring": return "Your access ends in 3 days";
    case "limit_request_approved": return "Your admin raised your limit";
    case "limit_request_declined": return "Your admin declined your request for more";
    case "research_limit_reached": return `${name}: scheduled refresh skipped, no researches left`;
    case "research_failed": return `${name}: research did not finish`;
    case "research_stalled": return `${name}: research stalled`;
    case "waiting_credit_limit": return `${name}: waiting to start`;
    case "research_finished": return `${name}: research finished`;
    case "library_added": return `${name}: added from the 6th Library`;
    default: return `${name}: refresh finished${what}`;
  }
}

function meta(n: Notification): string {
  switch (n.kind) {
    case "access_expiring": return "Using the workspace keeps it open.";
    case "limit_request_approved":
    case "limit_request_declined": return "See what is left on New research or in Ask.";
    case "research_limit_reached": return "Request more from your admin on New research.";
    case "research_failed": return n.reason ?? "Everything found so far is kept.";
    case "research_stalled": return "Everything found so far is kept. Open the account to resume it.";
    default: return "Open the account";
  }
}

function href(n: Notification): string {
  if (n.kind === "access_expiring") return "/";
  if (n.kind.startsWith("limit_request") || n.kind === "research_limit_reached") return "/research/new";
  return n.account_domain ? `/accounts/${encodeURIComponent(n.account_domain)}` : "/accounts";
}

/** The newest unread notifications as sidebar notes (at most `limit`). */
export function shellNotes(notifications: Notifications | null, limit = 3): ShellNote[] {
  return (notifications?.notifications ?? [])
    .filter((n) => !n.read)
    .slice(0, limit)
    .map((n) => ({ id: n.id, text: text(n), meta: meta(n), href: href(n), warn: WARNING_KINDS.has(n.kind) }));
}
