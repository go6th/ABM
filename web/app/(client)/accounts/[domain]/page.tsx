// The Account workspace as designed: breadcrumb, title and status line, Schedule and Refresh
// from web, THE ONE MINUTE VERSION (each sentence links to its source), Key findings on a first
// scan or What changed after a refresh, the account's composer, and on the right DEEP DIVE with
// live counts and FILES. Another client's account and a missing one look the same: not found.
import type { Metadata } from "next";
import Link from "next/link";
import type { AccountWorkspace, DocumentList } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { LINK_REL, safeLink } from "@/lib/render";
import { Grade, Pill, StateCard, type PillTone } from "@/ui/design";
import { FilesCard } from "@/components/accounts/FilesCard";
import { UploadedFilesCard } from "@/components/accounts/UploadedFilesCard";
import { AccountAsk } from "@/components/accounts/AccountAsk";
import { ResumeResearch } from "@/components/accounts/ResumeResearch";
import { ResearchProgress } from "@/components/accounts/ResearchProgress";

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

/** The first letter of a company, for its tile. */
function initialOf(name: string): string {
  return (name.replace(/^www\./i, "").match(/[a-z0-9]/i)?.[0] ?? "?").toUpperCase();
}

/** A gap reason's colour: something the user can act on is yellow, a failure rose, the rest
 *  (not available yet, outside this research's scope) neutral. */
function gapTone(reason: string): PillTone {
  const r = reason.toLowerCase();
  if (r.includes("failed") || r.includes("error")) return "bad";
  if (r.includes("only you") || r.includes("limit") || r.includes("waiting") || r.includes("waits")) return "warn";
  return "neutral";
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
        <Link className="back" href="/accounts">‹ Accounts</Link>
        {failed
          ? <StateCard title="This account could not be loaded" body="Your workspace could not be reached. Reload the page in a moment." action={<a className="btn" href={here}>Reload</a>} />
          : <StateCard title="Account not found" body="It is not in your workspace." action={<Link className="btn" href="/accounts">Back to accounts</Link>} />}
      </div>
    );
  }
  // the client's own uploads for this account (viewers may not list documents: none shown)
  let uploads: DocumentList["documents"] | null = null;
  if (ws.id) {
    try {
      uploads = (await serverGet<DocumentList>(`/v1/documents?account_id=${encodeURIComponent(ws.id)}`, here))?.documents ?? null;
    } catch (e) {
      if (!(e instanceof ServerApiError)) throw e;
    }
  }
  const name = ws.name ?? ws.domain;
  const findings = ws.findings ?? [];
  const sentences = ws.narrative?.sentences ?? [];
  const d = encodeURIComponent(ws.domain);
  const coverage = ws.counts.coverage_total ? `${ws.counts.coverage_done} of ${ws.counts.coverage_total} done` : "Not run yet";
  const rail: Array<[string, string, number | string]> = [
    ["Signals", `${here}/signals`, ws.counts.signals],
    ["People", `${here}/people`, ws.counts.people],
    ["Job postings", `${here}/jobs`, ws.counts.jobs],
    ["Tech stack and teams", `${here}/technology`, ws.counts.tech],
    ["News", `${here}/news`, ws.counts.news],
    ["From your mail and drive", `${here}/mail-and-drive`, ws.counts.mail_drive],
    ["Sources", `${here}/sources`, ws.counts.sources],
    ["Web searches", `${here}/searches`, ws.counts.searches ?? 0],
    ["Platform picture by lane", `${here}/facts`, ws.counts.facts ?? 0],
    ["Coverage", `${here}/coverage`, coverage],
  ];
  const glance: Array<[string, string, number]> = [
    ["Signals", `${here}/signals`, ws.counts.signals],
    ["People", `${here}/people`, ws.counts.people],
    ["Job postings", `${here}/jobs`, ws.counts.jobs],
    ["News", `${here}/news`, ws.counts.news],
  ];
  return (
    <div className="split">
      <div className="primary-col">
        <div className="head">
          <div className="ttl">
            <Link className="back" href="/accounts" prefetch={false}>‹ Accounts</Link>
            <div className="title-row">
              <span className="ti lg" aria-hidden="true">{initialOf(name)}</span>
              <div>
                <h1 className="big">{name}</h1>
                <div className="sub">{ws.subline}</div>
              </div>
            </div>
          </div>
          <Link className="btn" href={`/schedules?account=${d}#${d}`} prefetch={false}>Schedule</Link>
          <Link className="btn primary" href={`${here}/refresh`} prefetch={false}>Refresh from web</Link>
        </div>

        <nav className="kpi-tiles" aria-label="At a glance">
          {glance.map(([label, href, n], i) => (
            <Link key={label} className={i === 0 && Number(n) > 0 ? "kpi-tile hl" : "kpi-tile"} href={href} prefetch={false}>
              <span className="v">{n}</span>
              <span className="k">{label}</span>
            </Link>
          ))}
        </nav>

        {ws.open_job ? (
          <ResearchProgress key={`${ws.open_job.id}:${ws.open_job.run_id ?? ""}`} job={ws.open_job} scopeName={SCOPE_NAME[ws.open_job.scope] ?? "research"} />
        ) : ws.stopped_run ? <ResumeResearch stopped={ws.stopped_run} /> : (ws.gaps ?? []).length ? (
          <section className="card pad stack-14" aria-labelledby="gaps-title">
            <div className="summary-head">
              <h2 id="gaps-title">Research finished</h2>
              <span className="big-n">{coverage}</span>
            </div>
            {ws.counts.coverage_total ? (
              <progress className="pbar" max={ws.counts.coverage_total} value={ws.counts.coverage_done} aria-label={`${coverage} steps`} />
            ) : null}
            <ul className="chips" aria-label="Steps not done">
              {(ws.gaps ?? []).map((g) => (
                <li key={g.reason}><Pill tone={gapTone(g.reason)}><b>{g.count}</b>&nbsp;·&nbsp;{g.reason}</Pill></li>
              ))}
            </ul>
            <Link className="btn align-start" href={`${here}/coverage`} prefetch={false}>See each step in Coverage</Link>
          </section>
        ) : null}

        <section className="card pad-lg stack-10" aria-labelledby="omv">
          <div className="lbl" id="omv">The one minute version</div>
          {sentences.length ? (
            <>
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
              <div className="fine">Every statement links to its source in <Link href={`${here}/sources`}>Sources</Link>.</div>
            </>
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
                <div className="lrow"><Pill tone="neutral" small>No change</Pill><span className="grow">{joinAnd(ws.changes.quiet_lanes)} lanes</span><span className="when">This week</span></div>
              ) : null}
              {!ws.changes.rows?.length && !ws.changes.quiet_lanes?.length ? (
                <div className="lrow"><Pill tone="neutral" small>No change</Pill><span className="grow">Nothing material changed in the latest refresh</span></div>
              ) : null}
            </div>
          </section>
        ) : (
          <section className="stack-10" aria-labelledby="findings">
            <h2 id="findings">Key findings</h2>
            {findings.length ? (
              <div className="card list">
                {findings.map((r, i) => (
                  <div key={i} className="lrow"><Grade grade={r.grade} /><span className="grow">{r.text}</span><span className="when">{r.when}</span></div>
                ))}
              </div>
            ) : (
              <div className="card list"><div className="lrow mut">No findings yet.</div></div>
            )}
            {ws.first_scan ? (
              <div className="fine">This is the first scan, so there is nothing to compare yet. After the next refresh, a What changed section appears here.</div>
            ) : null}
          </section>
        )}

        <AccountAsk account={{ id: ws.id ?? "", domain: ws.domain, name }} />
      </div>

      <div className="side-col">
        <nav className="card rail" aria-label="Deep dive">
          <div className="lbl">Deep dive</div>
          {rail.map(([label, href, n]) => (
            <Link key={label} href={href} prefetch={false}>{label}<span className="n">{n}</span></Link>
          ))}
        </nav>
        <FilesCard domain={ws.domain} files={ws.files} />
        {uploads ? <UploadedFilesCard docs={uploads} /> : null}
      </div>
    </div>
  );
}
