// Every account screen's frame (redesign 2026-11-07): the company, one main action (Refresh), a
// … menu (Auto refresh, Delete account) and the tabs: Overview, People, Jobs,
// Technology, Signals, News, Sources, Files, then More for the rest of the deep-dive tables. It
// reads only the account's name (the light GET /v1/accounts/{domain}); an account that is not in
// the workspace draws no frame and the page says so.
import Link from "next/link";
import type { AccountDetail } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { PageTabs } from "@/ui/PageTabs";
import { AccountMore } from "@/components/accounts/AccountMore";
import { DeleteAccount } from "@/components/accounts/DeleteAccount";
import { RefreshDialog } from "@/components/accounts/RefreshDialog";

/** A path segment as text: a malformed escape ("%zz") is kept as typed instead of crashing. */
function segment(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function initialOf(name: string): string {
  return (name.replace(/^www\./i, "").match(/[a-z0-9]/i)?.[0] ?? "?").toUpperCase();
}

export default async function AccountLayout({ params, children }: { params: Promise<{ domain: string }>; children: React.ReactNode }) {
  const domain = segment((await params).domain);
  const here = `/accounts/${encodeURIComponent(domain)}`;
  let account: AccountDetail | null = null;
  try {
    account = await serverGet<AccountDetail>(`/v1/accounts/${encodeURIComponent(domain)}`, here, [404]);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
  }
  if (!account) return <>{children}</>;
  const name = account.name ?? account.domain;
  const d = encodeURIComponent(account.domain);
  const tabs = [
    { href: here, label: "Overview" },
    { href: `${here}/people`, label: "People" },
    { href: `${here}/jobs`, label: "Jobs" },
    { href: `${here}/technology`, label: "Technology" },
    { href: `${here}/signals`, label: "Signals" },
    { href: `${here}/news`, label: "News" },
    { href: `${here}/sources`, label: "Sources" },
    { href: `${here}/files`, label: "Files", prefix: true },
  ];
  const more: Array<[string, string]> = [
    ["Findings", `${here}/findings`],
    ["Platforms by area", `${here}/facts`],
    ["Platform teams", `${here}/teams`],
    ["Connections", `${here}/connections`],
    ["Calls made", `${here}/calls`],
    ["Notes", `${here}/notes`],
    ["Searches run", `${here}/searches`],
    ["Uploaded files", `${here}/uploaded-files`],
    ["Coverage", `${here}/coverage`],
    ["Archive", `${here}/archive`],
  ];
  return (
    <div className="acct">
      <div className="acct-top">
        <div className="acct-head">
          <div className="title-row">
            <span className="ti lg" aria-hidden="true">{initialOf(name)}</span>
            <div>
              <div className="crumb"><Link href="/accounts" prefetch={false}>Accounts</Link></div>
              <h1 className="big">{name}</h1>
              <div className="sub">{account.domain}</div>
            </div>
          </div>
          <div className="acct-actions">
            <RefreshDialog domain={account.domain} />
            <details className="dmenu">
              <summary className="btn icon" aria-label="More actions" title="More actions">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="12" r="1.8" /></svg>
              </summary>
              <div className="menu-list">
                <Link className="menu-item" href={`/settings/auto-refresh?account=${d}#${d}`} prefetch={false}>Auto refresh</Link>
                <DeleteAccount domain={account.domain} name={name} inMenu />
              </div>
            </details>
          </div>
        </div>
        <div className="acct-tabs">
          <PageTabs tabs={tabs} label={`${name} sections`} />
          <AccountMore items={more} />
        </div>
      </div>
      {children}
    </div>
  );
}
