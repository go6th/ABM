// The account frame: title, last researched, Refresh from web, the schedule line and the tabs.
// A missing account and another client's account render the same page.
import type { AccountDetail } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { AccountHeader } from "@/components/ws/AccountHeader";
import { AccountTabs } from "@/components/ws/AccountTabs";
import { EmptyState, LoadError } from "@/ui/primitives";
import { m } from "@/lib/messages";
import { day, runState } from "@/lib/ws-format";
import { cellText } from "@/lib/render";

export const dynamic = "force-dynamic";

export default async function AccountLayout({ params, children }: { params: Promise<{ domain: string }>; children: React.ReactNode }) {
  const domain = decodeURIComponent((await params).domain);
  const here = `/accounts/${encodeURIComponent(domain)}`;
  let account: AccountDetail | null = null;
  let reference: string | null | undefined;
  try {
    account = await serverGet<AccountDetail>(`/v1/accounts/${encodeURIComponent(domain)}`, here);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    reference = e.reference;
  }
  if (reference !== undefined) return <div className="page"><LoadError reference={reference} retryHref={here} /></div>;
  if (!account) {
    return <div className="page"><EmptyState title={m.account.notFoundTitle} body={m.account.notFoundBody} action={{ href: "/accounts", label: m.account.back }} /></div>;
  }
  const runs = [...account.runs].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const active = runs.find((r) => runState(r.status) === "running") ?? null;
  const lastDone = runs.find((r) => runState(r.status) === "done") ?? null;
  const fileMode = runs.some((r) => r.mode === "file") && !runs.some((r) => r.mode !== "file" && runState(r.status) === "done");
  return (
    <div className="page">
      <AccountHeader
        key={active?.run_id ?? "idle"}
        domain={account.domain}
        name={cellText(account.name ?? account.domain)}
        researched={lastDone ? m.account.refreshed(day(lastDone.finished_at ?? lastDone.created_at)) : m.account.notResearched}
        activeRunId={active?.run_id ?? null}
        activeStatus={active?.status ?? null}
        fileMode={fileMode}
      />
      <AccountTabs domain={account.domain} />
      {children}
    </div>
  );
}
