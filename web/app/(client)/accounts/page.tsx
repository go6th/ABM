// Accounts as designed: every account with its latest news in a sub-line (new signals, first
// scan complete, no change, researching, waiting to start), its lanes, when it was last
// researched and its refresh schedule. The whole row opens the account.
import type { Metadata } from "next";
import Link from "next/link";
import type { AccountPage } from "@/lib/gen/api.gen";
import { orderAccounts, subline } from "@/lib/accounts-list";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Accounts · Account Intelligence" };

export default async function AccountsPage() {
  let page: AccountPage | null = null;
  let failed = false;
  try {
    page = await serverGet<AccountPage>("/v1/accounts?limit=200", "/accounts");
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  const accounts = orderAccounts(page?.accounts ?? []);
  return (
    <div className="pg">
      <div className="head">
        <div className="ttl">
          <h1>Accounts</h1>
          <div className="sub">{accounts.length} account{accounts.length === 1 ? "" : "s"} in your workspace</div>
        </div>
        <Link className="btn primary" href="/accounts/add" prefetch={false}>Add account</Link>
      </div>
      {failed ? (
        <StateCard title="Accounts could not be loaded" body="Your workspace could not be reached. Reload the page in a moment." action={<Link className="btn" href="/accounts" prefetch={false}>Reload</Link>} />
      ) : accounts.length === 0 ? (
        <StateCard
          title="No accounts yet"
          body="Your workspace is your own. Research a company, add ready-made research, or analyze your own files."
          action={<span className="row-12 wrap"><Link className="btn primary" href="/accounts/add">Add your first account</Link><Link className="btn" href="/research/new?tab=file">Add files</Link></span>}
        />
      ) : (
        <div className="card tscroll">
          <div className="trow th cols-accounts" aria-hidden="true">
            <span>Account</span><span>Lanes</span><span>Last researched</span><span>Refresh</span><span />
          </div>
          <ul aria-label="Accounts">
            {accounts.map((a) => {
              const sub = subline(a);
              return (
                <li key={a.domain}>
                  <Link className="trow cols-accounts" href={`/accounts/${encodeURIComponent(a.domain)}`} prefetch={false}>
                    <span className="strong">
                      {a.name ?? a.domain}
                      <span className={`sub ${sub.tone}`}>{sub.text}</span>
                    </span>
                    <span className="mut">{a.lanes?.length ? a.lanes.join(", ") : "No lanes yet"}</span>
                    <span>{a.last_researched_label}</span>
                    <span className="mut">{a.cadence_label}</span>
                    <span className="chev" aria-hidden="true">›</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
