// Accounts (redesign 2026-11-07): one table of every account with its status in plain words, the
// platforms seen, when it was last researched and its auto refresh, edited in the row. Search by
// name or website (?q=) and filter by status (?status=). The account name opens the account.
import type { Metadata } from "next";
import Link from "next/link";
import type { AccountPage, Schedules } from "@/lib/gen/api.gen";
import { orderAccounts, subline, type AccountItem } from "@/lib/accounts-list";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { Pill, StateCard, type PillTone } from "@/ui/design";
import { AutoRefreshSelect } from "@/components/accounts/AutoRefreshSelect";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Accounts · Account Intelligence" };

const TONE: Record<string, PillTone> = { ok: "ok", run: "run", warn: "warn", neutral: "neutral", "": "neutral" };

// the status filters, each with the account states it shows
const FILTERS: { key: string; label: string; states: string[] | null }[] = [
  { key: "all", label: "All", states: null },
  { key: "new", label: "New signals", states: ["new_signals"] },
  { key: "running", label: "Researching", states: ["researching", "waiting_credit_limit"] },
  { key: "attention", label: "Did not finish", states: ["failed"] },
  { key: "new-account", label: "Not researched", states: ["not_researched"] },
];

/** The first letter of a company, for its tile. */
function initialOf(name: string): string {
  return (name.replace(/^www\./i, "").match(/[a-z0-9]/i)?.[0] ?? "?").toUpperCase();
}

function matches(a: AccountItem, q: string): boolean {
  return !q || `${a.name ?? ""} ${a.domain}`.toLowerCase().includes(q);
}

export default async function AccountsPage({ searchParams }: { searchParams: Promise<{ q?: string; status?: string }> }) {
  const params = await searchParams;
  const q = (params.q ?? "").trim().toLowerCase().slice(0, 100);
  const status = FILTERS.find((f) => f.key === params.status) ?? FILTERS[0]!;
  // the two reads are independent; the list still shows when the schedules cannot be read
  const [listRead, schedRead] = await Promise.allSettled([
    serverGet<AccountPage>("/v1/accounts?limit=200", "/accounts"),
    serverGet<Schedules>("/v1/schedules", "/accounts"),
  ]);
  if (listRead.status === "rejected" && !(listRead.reason instanceof ServerApiError)) throw listRead.reason;
  const failed = listRead.status === "rejected";
  const all = orderAccounts(listRead.status === "fulfilled" ? listRead.value?.accounts ?? [] : []);
  const schedules = new Map((schedRead.status === "fulfilled" ? schedRead.value?.rows ?? [] : []).map((r) => [r.domain, r]));
  const searched = all.filter((a) => matches(a, q));
  const shown = searched.filter((a) => !status.states || status.states.includes(a.state ?? "not_researched"));
  const count = (f: (typeof FILTERS)[number]) => (f.states ? searched.filter((a) => f.states!.includes(a.state ?? "not_researched")).length : searched.length);
  const href = (change: Record<string, string>) => {
    const next = new URLSearchParams({ ...(q ? { q } : {}), ...(status.key !== "all" ? { status: status.key } : {}), ...change });
    for (const [k, v] of [...next.entries()]) if (!v || (k === "status" && v === "all")) next.delete(k);
    const s = next.toString();
    return s ? `/accounts?${s}` : "/accounts";
  };
  return (
    <div className="pg">
      <div className="head">
        <div className="ttl">
          <h1>Accounts</h1>
          <div className="sub">{all.length} account{all.length === 1 ? "" : "s"} in your workspace. Open one to see its brief.</div>
        </div>
        <div className="row-12 wrap">
          <Link className="btn" href="/research?tab=file" prefetch={false}>Import a file</Link>
          <Link className="btn primary" href="/accounts/add" prefetch={false}>Add account</Link>
        </div>
      </div>
      {failed ? (
        <StateCard title="Accounts could not be loaded" body="Your workspace could not be reached. Reload the page in a moment." action={<Link className="btn" href="/accounts" prefetch={false}>Reload</Link>} />
      ) : all.length === 0 ? (
        <StateCard
          title="No accounts yet"
          body="Your workspace is your own. Research a company, add ready-made research, or analyze your own files."
          action={<span className="row-12 wrap"><Link className="btn primary" href="/accounts/add">Add your first account</Link><Link className="btn" href="/research?tab=file">Add files</Link></span>}
        />
      ) : (
        <>
          <div className="list-bar">
            <form className="list-search" action="/accounts" role="search">
              {status.key !== "all" ? <input type="hidden" name="status" value={status.key} /> : null}
              <label className="sr-only" htmlFor="acct-q">Search accounts</label>
              <input id="acct-q" className="input sm" type="search" name="q" defaultValue={params.q ?? ""} placeholder="Search accounts" maxLength={100} />
            </form>
            <nav className="fchips" aria-label="Filter by status">
              {FILTERS.filter((f) => f.key === "all" || f.key === status.key || count(f) > 0).map((f) => (
                <Link key={f.key} className="fc" href={href({ status: f.key })} aria-current={f.key === status.key ? "true" : undefined} prefetch={false} scroll={false}>
                  {f.label} <span className="n">{count(f)}</span>
                </Link>
              ))}
            </nav>
          </div>
          {shown.length === 0 ? (
            <StateCard title="No accounts match" body="Try another name or clear the filter." action={<Link className="btn" href="/accounts" prefetch={false}>Show all accounts</Link>} />
          ) : (
            <div className="card clip tscroll">
              <table className="dt accounts-table">
                <thead>
                  <tr><th scope="col">Account</th><th scope="col">Status</th><th scope="col">Platforms seen</th><th scope="col">Last research</th><th scope="col">Auto refresh</th></tr>
                </thead>
                <tbody>
                  {shown.map((a, i) => {
                    const sub = subline(a);
                    const label = a.name ?? a.domain;
                    const sched = schedules.get(a.domain);
                    return (
                      <tr key={a.domain}>
                        <td>
                          <Link className="acct-cell" href={`/accounts/${encodeURIComponent(a.domain)}`} prefetch={false}>
                            <span className={i % 2 ? "ti lav" : "ti"} aria-hidden="true">{initialOf(label)}</span>
                            <span className="nm"><b>{label}</b><span className="mut xs-text">{a.domain}</span></span>
                          </Link>
                        </td>
                        <td data-label="Status"><Pill tone={TONE[sub.tone] ?? "neutral"} small>{sub.text}</Pill></td>
                        <td data-label="Platforms seen" className="mut">{a.lanes?.length ? a.lanes.join(", ") : "None seen yet"}</td>
                        <td data-label="Last research">{a.last_researched_label}</td>
                        <td data-label="Auto refresh">{sched ? <AutoRefreshSelect row={sched} /> : <span className="mut">{a.cadence_label}</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="mut sm-text">Each auto refresh uses one research from your monthly allowance when it runs.</p>
        </>
      )}
    </div>
  );
}
