// The account's Overview tab (redesign 2026-11-07; the frame, actions and tabs are the account
// layout's): research status, the summary (each sentence links to its source), Key findings on a
// first scan or What changed after a refresh, and on the right At a glance, coverage and the
// account's Ask box. The delivered files and uploads live on the Files tab. Another client's
// account and a missing one look the same: not found.
import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import type { AccountWorkspace, DocumentList } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { LINK_REL, safeLink } from "@/lib/render";
import { Grade, Pill, StateCard } from "@/ui/design";
import { LiveSync, RetryRefresh, TryAgain } from "@/components/accounts/LiveSync";
import { AccountAsk } from "@/components/accounts/AccountAsk";
import { ResumeResearch } from "@/components/accounts/ResumeResearch";
import { ResearchProgress } from "@/components/accounts/ResearchProgress";
import { ImportProblems } from "@/components/accounts/ImportProblems";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ domain: string }> };

/** A path segment as text: a malformed escape ("%zz") is kept as typed instead of crashing. */
function segment(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const domain = segment((await params).domain);
  return { title: `${domain} · Account Intelligence` };
}

function joinAnd(items: string[]): string {
  return items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

const SCOPE_NAME: Record<string, string> = { jobs: "Job postings only", people: "People and teams", full: "Full research", topic: "a specific topic", file: "your files" };

/** Keeps the page current while the account's uploads are read (the counts and summary follow
 *  what a work zip holds), in its own Suspense boundary so the page does not wait for it. The
 *  uploads themselves are listed on the Files tab. Viewers may not list documents (403): for them
 *  there are no uploads to follow, which is not an error. A list that could not be read is asked
 *  for again by itself. */
async function UploadWatch({ accountId, here }: { accountId: string; here: string }) {
  let uploads: DocumentList["documents"] | null = null;
  try {
    uploads = (await serverGet<DocumentList>(`/v1/documents?account_id=${encodeURIComponent(accountId)}`, here, [403]))?.documents ?? null;
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    return <RetryRefresh />;
  }
  return uploads ? <LiveSync accountId={accountId} docs={uploads} /> : null;
}

export default async function AccountWorkspacePage({ params }: Params) {
  const domain = segment((await params).domain);
  const here = `/accounts/${encodeURIComponent(domain)}`;
  let ws: AccountWorkspace | null = null;
  let failed = false;
  try {
    ws = await serverGet<AccountWorkspace>(`/v1/accounts/${encodeURIComponent(domain)}/workspace`, here);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  if (failed || !ws) {
    return (
      <div className="pg">
        {failed
          ? <><StateCard title="This account could not be loaded" body="Your workspace could not be reached. The page tries again by itself." action={<TryAgain />} /><RetryRefresh /></>
          : <StateCard title="Account not found" body="It is not in your workspace." action={<Link className="btn" href="/accounts">Back to accounts</Link>} />}
      </div>
    );
  }
  const name = ws.name ?? ws.domain;
  const findings = ws.findings ?? [];
  const sentences = ws.narrative?.sentences ?? [];
  const coverage = ws.counts.coverage_total ? `${ws.counts.coverage_done} of ${ws.counts.coverage_total} areas covered` : null;
  const glance: Array<[string, string, number]> = [
    ["People", `${here}/people`, ws.counts.people],
    ["Job postings", `${here}/jobs`, ws.counts.jobs],
    ["Signals", `${here}/signals`, ws.counts.signals],
    ["News", `${here}/news`, ws.counts.news],
    ["Sources", `${here}/sources`, ws.counts.sources],
  ];
  return (
    <div className="split">
      <div className="primary-col">
        <p className="mut sm-text">{ws.subline.replace(/\s*·\s*Web: not used for answers\s*$/, "")}</p>
        <ImportProblems problems={ws.import_problems ?? []} sheetHref={`${here}/uploaded-files`} />

        {ws.open_job ? (
          <ResearchProgress key={`${ws.open_job.id}:${ws.open_job.run_id ?? ""}`} job={ws.open_job} scopeName={SCOPE_NAME[ws.open_job.scope] ?? "research"} />
        ) : ws.stopped_run ? <ResumeResearch stopped={ws.stopped_run} /> : null}

        <section className="stack-10" aria-labelledby="omv">
          <h2 id="omv">Summary</h2>
          {sentences.length ? (
            <p className="narr">
              {sentences.map((s, i) => {
                const href = safeLink(s.source_url ?? "");
                return (
                  <span key={i}>
                    {s.text}
                    {href ? (
                      <a className="cite" href={href} target="_blank" rel={LINK_REL} aria-label={`Source ${i + 1}: ${s.source_title ?? "source"}`}>{i + 1}</a>
                    ) : null}
                    {i < sentences.length - 1 ? " " : ""}
                  </span>
                );
              })}
            </p>
          ) : (
            <p className="mut">A summary appears here when the first research finishes.</p>
          )}
        </section>

        {ws.changes ? (
          <section className="stack-10" aria-labelledby="changed">
            <h2 id="changed">What changed since {ws.changes.since}</h2>
            <div className="card list">
              {(ws.changes.rows ?? []).map((r, i) => (
                <div key={i} className="lrow"><Grade grade={r.grade} /><span className="grow">{r.text}</span><span className="when">{r.when}</span></div>
              ))}
              {ws.changes.quiet_lanes?.length ? (
                <div className="lrow"><Pill tone="neutral" small>No change</Pill><span className="grow">{joinAnd(ws.changes.quiet_lanes)}</span><span className="when">This week</span></div>
              ) : null}
              {!ws.changes.rows?.length && !ws.changes.quiet_lanes?.length ? (
                <div className="lrow"><Pill tone="neutral" small>No change</Pill><span className="grow">Nothing material changed in the latest refresh</span></div>
              ) : null}
            </div>
          </section>
        ) : (
          <section className="stack-10" aria-labelledby="findings">
            <div className="between">
              <h2 id="findings">Key findings</h2>
              {ws.counts.findings ? <Link className="link-strong sm-text" href={`${here}/findings`} prefetch={false}>See all {ws.counts.findings} findings</Link> : null}
            </div>
            {findings.length ? (
              <div className="card list">
                {findings.map((r, i) => (
                  <div key={i} className="lrow"><Grade grade={r.grade} /><span className="grow">{r.text}</span><span className="when">{r.when}</span></div>
                ))}
              </div>
            ) : (
              <div className="card list"><div className="lrow mut">No findings yet.</div></div>
            )}
            {ws.first_scan ? <div className="fine">This is the first scan; after the next refresh, What changed appears here.</div> : null}
          </section>
        )}
        {ws.id ? <Suspense fallback={null}><UploadWatch accountId={ws.id} here={here} /></Suspense> : null}
      </div>

      <aside className="side-col stack-22" aria-label="At a glance">
        <section className="stack-10">
          <h2 className="h-sm">At a glance</h2>
          <nav className="card glance" aria-label="Tables">
            {glance.map(([label, href, n]) => (
              <Link key={label} href={href} prefetch={false}><span>{label}</span><b>{n.toLocaleString()}</b></Link>
            ))}
          </nav>
        </section>
        {coverage ? (
          <section className="stack-10">
            <h2 className="h-sm">Research coverage</h2>
            <div className="card pad stack-10">
              <b>{coverage}</b>
              <progress className="pbar" max={ws.counts.coverage_total} value={ws.counts.coverage_done} aria-label={coverage} />
              {(ws.gaps ?? []).length ? (
                <ul className="gap-list">
                  {(ws.gaps ?? []).map((g) => <li key={g.reason}><b>{g.count}</b> {g.reason}</li>)}
                </ul>
              ) : null}
              <Link className="link-strong sm-text" href={`${here}/coverage`} prefetch={false}>See each area</Link>
            </div>
          </section>
        ) : null}
        <section className="stack-10">
          <h2 className="h-sm">Ask about {name}</h2>
          <AccountAsk account={{ id: ws.id ?? "", domain: ws.domain, name }} />
        </section>
      </aside>
    </div>
  );
}
