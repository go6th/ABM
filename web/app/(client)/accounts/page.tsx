// Accounts: a table of account, last researched, schedule and new signals. Click a row to open
// it. One button: New research.
import Link from "next/link";
import type { AccountItem, AccountPage, SignalPage } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { EmptyState, LoadError, PageHead } from "@/ui/primitives";
import { m } from "@/lib/messages";
import { ago, runState } from "@/lib/ws-format";
import { cellText } from "@/lib/render";
import { AccountRows } from "@/components/ws/AccountRows";

export const dynamic = "force-dynamic";

function researched(a: AccountItem): { text: string; tone: "ok" | "warn" | "bad" | "neutral" } {
  const state = runState(a.latest_run?.status);
  if (state === "running") return { text: a.latest_run?.status.toUpperCase() === "AWAITING_APPROVAL" ? m.accounts.paused : m.accounts.running, tone: "warn" };
  if (state === "failed") return { text: m.accounts.failed, tone: "bad" };
  if (state === "done") return { text: ago(a.latest_run?.created_at), tone: "ok" };
  return { text: m.accounts.never, tone: "neutral" };
}

/** New signals per account in the last seven days (counts only). */
function freshSignals(items: SignalPage["items"], now = Date.now()): Map<string, number> {
  const weekAgo = now - 7 * 86_400_000;
  const fresh = new Map<string, number>();
  for (const s of items) {
    if (s.sensitive || new Date(s.detected_at).getTime() < weekAgo) continue;
    fresh.set(s.account, (fresh.get(s.account) ?? 0) + 1);
  }
  return fresh;
}

export default async function Accounts() {
  let page: AccountPage | null = null;
  let signals: SignalPage | null = null;
  let reference: string | null | undefined;
  try {
    [page, signals] = await Promise.all([serverGet<AccountPage>("/v1/accounts?limit=200", "/accounts"), serverGet<SignalPage>("/v1/signals", "/accounts")]);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    reference = e.reference;
  }
  const accounts = (page?.accounts ?? []).slice().sort((a, b) => (b.latest_run?.created_at ?? "").localeCompare(a.latest_run?.created_at ?? "") || a.domain.localeCompare(b.domain));
  const fresh = freshSignals(signals?.items ?? []);
  const rows = accounts.map((a) => {
    const r = researched(a);
    return { domain: a.domain, name: cellText(a.name ?? a.domain), researched: r.text, tone: r.tone, signals: fresh.get(a.domain) ?? 0 };
  });
  return (
    <div className="page">
      <PageHead title={m.accounts.title} sub={m.accounts.sub(accounts.length)} actions={<Link className="btn primary" href="/research/new">{m.accounts.newResearch}</Link>} />
      {reference !== undefined ? <LoadError reference={reference} retryHref="/accounts" /> : accounts.length === 0 ? (
        <EmptyState title={m.accounts.emptyTitle} body={m.accounts.emptyBody} />
      ) : (
        <AccountRows rows={rows} />
      )}
    </div>
  );
}
