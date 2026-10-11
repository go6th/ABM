// Account workspace: the account, its research state, what we know and what changed, then a
// composer for questions. A missing account and another client's account render the same page.
import type { AccountDetail, History, Me, TablePage } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { GradeMark, toGrade } from "@/components/ws/GradeMark";
import { DeepDiveLinks } from "@/components/ws/DeepDiveLinks";
import { RefreshDialog } from "@/components/ws/RefreshDialog";
import { RunStatus } from "@/components/ws/RunStatus";
import { AccountAsk } from "@/components/ws/AccountAsk";
import { Downloads } from "@/components/ws/Downloads";
import { Banner, EmptyState, LoadError, Monogram } from "@/ui/primitives";
import { m } from "@/lib/messages";
import { day, humanize, runState } from "@/lib/ws-format";
import { cellText } from "@/lib/render";

export const dynamic = "force-dynamic";

const KNOWN_LIMIT = 6;

export default async function AccountWorkspace({ params }: { params: Promise<{ domain: string }> }) {
  const domain = decodeURIComponent((await params).domain);
  const base = `/v1/accounts/${encodeURIComponent(domain)}`;
  const here = `/accounts/${encodeURIComponent(domain)}`;
  let account: AccountDetail | null = null;
  let facts: TablePage | null = null;
  let history: History | null = null;
  let me: Me | null = null;
  let settings: { settings: { pursuing_client?: string | null } } | null = null;
  let reference: string | null | undefined;
  try {
    account = await serverGet<AccountDetail>(base, here);
    if (account) {
      [facts, history, me, settings] = await Promise.all([
        serverGet<TablePage>(`${base}/tables/facts`, here, [409]),
        serverGet<History>(`${base}/history`, here),
        serverGet<Me>("/v1/me", here),
        serverGet<{ settings: { pursuing_client?: string | null } }>("/v1/settings", here, [403]),
      ]);
    }
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    reference = e.reference;
  }
  if (reference !== undefined) return <div className="reading"><LoadError reference={reference} retryHref={here} /></div>;
  if (!account) {
    return (
      <div className="reading">
        <EmptyState title={m.workspace.notFoundTitle} body={m.workspace.notFoundBody} action={{ href: "/accounts", label: m.workspace.backToAccounts }} />
      </div>
    );
  }

  const name = account.name ?? account.domain;
  const runs = [...account.runs].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const active = runs.find((r) => runState(r.status) === "running") ?? null;
  const lastDone = runs.find((r) => runState(r.status) === "done") ?? null;
  const lastFailed = runs[0] && runState(runs[0].status) === "failed" ? runs[0] : null;
  const known = (facts?.rows ?? [])
    .map((r) => ({ id: r.row_id ?? String(r.position), grade: toGrade(r.cells.grade), finding: cellText(r.cells.finding, 400), question: cellText(r.cells.question, 300) }))
    .filter((f) => f.finding && f.grade)
    .sort((a, b) => ["confirmed", "probable", "hypothesis", "lead"].indexOf(a.grade!) - ["confirmed", "probable", "hypothesis", "lead"].indexOf(b.grade!))
    .slice(0, KNOWN_LIMIT);
  const changed = (history?.history ?? []).slice(0, 5);

  return (
    <div className="content">
      <div className="account-head">
        <Monogram name={account.domain} />
        <div>
          <h1>{cellText(name)}</h1>
          <p className="sub">
            {account.name ? <>{cellText(account.domain)}. </> : null}
            {lastDone ? m.workspace.refreshed(day(lastDone.finished_at ?? lastDone.created_at)) : m.workspace.notResearched}
          </p>
        </div>
        {me?.capabilities.includes("runs:create") ? (
          <div className="actions">
            <RefreshDialog domain={account.domain} disabled={active !== null} pursuing={settings?.settings.pursuing_client ?? null} />
          </div>
        ) : null}
      </div>
      <DeepDiveLinks domain={account.domain} />

      {active ? <div className="block-gap"><RunStatus runId={active.run_id} startedAt={active.created_at} /></div> : null}
      {!active && lastFailed ? <div className="block-gap"><Banner kind="error" role="status">{m.workspace.runFailed}</Banner></div> : null}

      <section className="section" aria-labelledby="known">
        <h2 id="known">{m.workspace.known}</h2>
        {known.length ? (
          <ul className="facts">
            {known.map((f) => (
              <li key={f.id}>
                <GradeMark grade={f.grade!} />
                <div>
                  <p className="claim">{f.finding}</p>
                  {f.grade === "hypothesis" && f.question ? <p className="t-footnote ink-2">{f.question}</p> : null}
                </div>
              </li>
            ))}
          </ul>
        ) : <p className="ink-2">{m.workspace.knownEmpty}</p>}
      </section>

      {lastDone ? (
        <section className="section" aria-labelledby="changed">
          <h2 id="changed">{m.workspace.changed}</h2>
          {changed.length ? (
            <ul className="rows">
              {changed.map((c) => {
                const grade = toGrade(c.grade);
                return (
                  <li key={c.fact_id} className="list-row">
                    <div>
                      <p>{humanize(c.signal_type)}{c.attribute ? `: ${cellText(c.attribute, 120)}` : ""}</p>
                      <p className="context">{cellText(c.reason, 200)}</p>
                    </div>
                    <div className="meta">{grade ? <GradeMark grade={grade} /> : null}<span>{day(c.expired_at)}</span></div>
                  </li>
                );
              })}
            </ul>
          ) : <p className="ink-2">{m.workspace.changedEmpty}</p>}
        </section>
      ) : null}

      {lastDone ? <Downloads domain={account.domain} /> : null}

      <AccountAsk name={cellText(name)} />
    </div>
  );
}
