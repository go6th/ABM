// Accounts as designed: every account with its latest news in a sub-line (new signals, first
// scan complete, no change, researching, waiting to start), its lanes, when it was last
// researched and its refresh schedule. The whole row opens the account.
import type { Metadata } from "next";
import Link from "next/link";
import type { AccountPage } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Accounts · Account Intelligence" };

type Item = AccountPage["accounts"][number];

function subline(a: Item): { text: string; tone: string } {
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

export default async function AccountsPage() {
  let page: AccountPage | null = null;
  let failed = false;
  try {
    page = await serverGet<AccountPage>("/v1/accounts?limit=200", "/accounts");
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  // Most news first, then most recently researched; on the same day an account that has been
  // refreshed before one with only a first scan, then by name.
  const watched = (a: Item) => (a.state === "new_signals" || a.state === "no_change" ? 0 : 1);
  const accounts = [...(page?.accounts ?? [])].sort((x, y) =>
    (y.new_signals ?? 0) - (x.new_signals ?? 0)
    || (y.last_researched_at ?? "").slice(0, 10).localeCompare((x.last_researched_at ?? "").slice(0, 10))
    || watched(x) - watched(y)
    || (x.name ?? x.domain).localeCompare(y.name ?? y.domain));
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
          body="Research a company, add ready-made research shared with your team, or analyze your own file."
          action={<Link className="btn primary" href="/accounts/add">Add account</Link>}
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
