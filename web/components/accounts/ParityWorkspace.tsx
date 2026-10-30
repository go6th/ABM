"use client";
// The delivered files as tabs, drawn from the account's parity bundle version: one tab per file
// with its tables in the file's order (the Word files' prose above them), a download button per
// tab, "Download all" (the workspace zip, which never holds the internal-only file) and the
// Summary counts computed by the shared count module, asserted equal to the manifest's
// recalculated values; a difference shows an error banner instead of a number.
import { useState } from "react";
import type { ParityScreen, ParityTableRef } from "@/lib/gen/api.gen";
import { clean } from "@/lib/parity/clean";
import { jobsRowTotals, jobsSummary, meetsSeniority, mismatches, peopleLaneCounts, peopleLevelCounts, rankCounts, summaryValues, type CountBundle } from "@/lib/parity/counts";
import { FILES, FILE_ORDER } from "@/lib/parity/tableSpecs";
import { ParityTable } from "./ParityTable";

type Prose = Record<string, unknown>;
const str = (v: unknown): string => (v == null ? "" : String(v));
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

export function fileHref(domain: string, version: number, name: string): string {
  return `/v1/accounts/${encodeURIComponent(domain)}/parity/files/${encodeURIComponent(name)}?version=${version}`;
}

/** The count bundle the shared count module reads: the meta block and the row projections. */
export function countBundle(screen: ParityScreen): CountBundle {
  const meta = screen.meta as CountBundle["meta"];
  return {
    meta,
    jobs_rows: screen.count_rows.jobs_rows as CountBundle["jobs_rows"],
    people_rows: screen.count_rows.people_rows as CountBundle["people_rows"],
    ranking: screen.ranking as CountBundle["ranking"],
  };
}

function Paragraphs({ items, kind = "p" }: { items: unknown[]; kind?: "p" | "li" | "ol" }) {
  const texts = items.map((t) => clean(Array.isArray(t) ? t.map(str).join(". ") : str(t)));
  if (kind === "p") return <>{texts.map((t, i) => <p key={i}>{t}</p>)}</>;
  if (kind === "ol") return <ol>{texts.map((t, i) => <li key={i}>{t}</li>)}</ol>;
  return <ul>{texts.map((t, i) => <li key={i}>{t}</li>)}</ul>;
}

function SummaryProse({ prose }: { prose: Prose }) {
  const narrative = obj(prose.narrative);
  return (
    <div className="card parity-prose">
      <p>{clean(str(prose.glossary))}</p>
      <h3>1. The account in plain words</h3>
      <h4>The account picture</h4>
      <Paragraphs items={list(narrative.picture)} />
      <h4>What stands out</h4>
      <Paragraphs items={list(narrative.stands_out)} kind="li" />
      <h4>What it likely means for each lane (interpretation labeled)</h4>
      <ul>{list(narrative.lanes).map((r, i) => { const [k, t] = r as [string, string]; return <li key={i}><b>{clean(k)}.</b> {clean(t)}</li>; })}</ul>
      <h4>How this could be wrong</h4>
      <Paragraphs items={list(narrative.wrong)} kind="li" />
      <h4>The simplest way to sharpen it</h4>
      <Paragraphs items={list(narrative.sharpen)} kind="ol" />
      <h3>3. Company and transformation baseline</h3>
      <Paragraphs items={list(prose.baseline)} kind="li" />
      <h3>Hiring, people and hypotheses</h3>
      <p>{clean(str(prose.hiring_interpretation))}</p>
      <p>{clean(str(obj(prose.people_text).built))}</p>
      <p>{clean(str(obj(prose.people_text).exclusions))}</p>
      <Paragraphs items={list(prose.people_interpretation)} kind="li" />
      <ol>{list(prose.hypotheses).map((r, i) => { const [h, q] = r as [string, string]; return <li key={i}>{clean(h)} <i>Confirming question: {clean(q)}</i></li>; })}</ol>
      <h4>Cautions for sensitive findings</h4>
      <Paragraphs items={list(prose.cautions)} kind="li" />
      <h4>Questions for a first conversation</h4>
      <Paragraphs items={list(prose.first_call)} kind="ol" />
      <h4>Decisions waiting on the account owner</h4>
      <Paragraphs items={list(prose.waiting)} kind="li" />
    </div>
  );
}

function HighlightsProse({ prose }: { prose: Prose }) {
  const h = obj(prose.lane_highlights);
  return (
    <div className="card parity-prose">
      <p>{clean(str(h.subtitle))}</p>
      {list(h.sections).map((s, i) => { const sec = obj(s); return <div key={i}><h3>{clean(str(sec.heading))}</h3><Paragraphs items={list(sec.bullets)} kind="li" /></div>; })}
    </div>
  );
}

function InternalProse({ prose, meta }: { prose: Prose; meta: Record<string, unknown> }) {
  const io = obj(prose.internal_only);
  return (
    <div className="card parity-prose">
      <p className="pill internal big">Internal only. Never in the client share or the workspace zip.</p>
      <p>Internal notes from the pursuing client&apos;s own records, {str(io.dates)}.</p>
      <h3>What was found in the {str(meta.pursuing_client_display)}&apos;s records</h3>
      <ul>{list(io.items).map((r, i) => { const [k, t] = r as [string, string]; return <li key={i}><b>{k}</b> {t}</li>; })}</ul>
      <h3>Why it matters</h3>
      <Paragraphs items={list(io.why)} kind="li" />
      <h3>What the client facing files say instead</h3>
      <p>{str(io.instead)}</p>
    </div>
  );
}

function Counts({ screen }: { screen: ParityScreen }) {
  const b = countBundle(screen);
  const computed = summaryValues(b);
  const wrong = mismatches(computed, screen.summary as Record<string, Record<string, unknown>>);
  const jobs = jobsSummary(b);
  const totals = jobsRowTotals(b);
  const levels = peopleLevelCounts(b);
  const lanes = peopleLaneCounts(b);
  const ranks = rankCounts(b);
  return (
    <div className="card parity-counts" data-counts={wrong.length ? "mismatch" : "ok"}>
      {wrong.length ? (
        <div className="warn-bar" role="alert" data-testid="counts-mismatch">
          The screen&apos;s counts differ from the delivered files on {wrong.length} value{wrong.length === 1 ? "" : "s"}: {wrong.slice(0, 6).join(", ")}{wrong.length > 6 ? ", …" : ""}. The files are the record; this view is not to be quoted.
        </div>
      ) : (
        <p className="mut small" data-testid="counts-ok">Every count below equals the recalculated Summary sheets of the delivered workbooks.</p>
      )}
      <div className="parity-count-grid">
        <table className="xl" data-table="counts_jobs">
          <caption>Platform job postings, {str(screen.meta.account_short)} positions only</caption>
          <thead><tr><th scope="col">Platform</th><th scope="col">Total</th><th scope="col">Closed</th><th scope="col">Open</th><th scope="col">Build role</th><th scope="col">Admin and owner role</th><th scope="col">Working tool</th><th scope="col">User role</th></tr></thead>
          <tbody>
            {Object.entries(jobs).map(([lane, c]) => <tr key={lane}><th scope="row">{lane}</th><td className="c">{c.total}</td><td className="c">{c.closed}</td><td className="c">{c.open}</td><td className="c">{c.build}</td><td className="c">{c.admin}</td><td className="c">{c.tool}</td><td className="c">{c.user}</td></tr>)}
            <tr><th scope="row">Rows on the Jobs sheet (all entities)</th><td className="c">{totals.all_rows}</td><td colSpan={6} /></tr>
            <tr><th scope="row">Rows that are {str(screen.meta.account_short)} positions</th><td className="c">{totals.account_rows}</td><td colSpan={6} /></tr>
            <tr><th scope="row">Rows that are not {str(screen.meta.account_short)} positions</th><td className="c">{totals.other_rows}</td><td colSpan={6} /></tr>
          </tbody>
        </table>
        <table className="xl" data-table="counts_people">
          <caption>People by level and lane (senior people only)</caption>
          <thead><tr><th scope="col">Level</th><th scope="col">People</th><th scope="col">Priority A</th></tr></thead>
          <tbody>
            {Object.entries(levels).map(([label, c]) => <tr key={label}><th scope="row">{label}</th><td className="c">{c.people}</td><td className="c">{c.priority_a}</td></tr>)}
            <tr><th scope="row">Meets seniority rule</th><td className="c">{meetsSeniority(b)}</td><td /></tr>
            <tr><th scope="col">Lane, senior people only</th><th scope="col">A / B / C</th><th scope="col">Total</th></tr>
            {Object.entries(lanes).map(([lane, c]) => <tr key={lane}><th scope="row">{lane}</th><td className="c">{c.a} / {c.b} / {c.c}</td><td className="c">{c.total}</td></tr>)}
            <tr><th scope="row">Platform ranks (A / B / C)</th><td className="c">{ranks.A} / {ranks.B} / {ranks.C}</td><td className="c">{str(screen.ranking.base)} base</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function ParityWorkspace({ domain, screen }: { domain: string; screen: ParityScreen }) {
  const files = screen.files.filter((f) => f.file && f.file !== "workspace");
  const order = FILE_ORDER.filter((k) => files.some((f) => f.file === k));
  const [tab, setTab] = useState<string>(order[0] ?? "summary");
  const tables = screen.tables.filter((t: ParityTableRef) => t.file === tab);
  const current = files.find((f) => f.file === tab);
  const prose = screen.prose as Prose;
  const zip = screen.download_all;
  return (
    <section className="parity" aria-labelledby="parity-title" data-version={screen.version}>
      <div className="sheet-bar">
        <h2 id="parity-title" className="h2">Delivered files</h2>
        <span className="mut small">Version {screen.version} · {screen.created_at.slice(0, 10)}{screen.current === false ? " · an earlier research" : ""}</span>
        <span className="spacer" />
        {zip ? <a className="btn primary sm" href={zip} download data-testid="download-all">Download all</a> : null}
      </div>
      {screen.current === false ? (
        <p className="warn-bar" role="note" data-testid="parity-earlier">
          These are the files of an earlier research. The Files card shows the files of the latest one, so the rows and counts here can differ from the file tabs of the deep dive.
        </p>
      ) : null}
      <p className="mut small" data-testid="parity-scope">
        The counts on this card are the rows of the delivered files. The Deep dive list counts everything the research holds, so a number there can be larger.
      </p>
      <nav className="sheet-tabs" aria-label="Delivered files">
        {order.map((k) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} aria-current={tab === k ? "page" : undefined} onClick={() => setTab(k)} data-file-tab={k}>
            {FILES[k]?.label ?? k}
          </button>
        ))}
      </nav>
      <div className="stack-10 parity-panel" role="tabpanel" data-file={tab}>
        {current ? (
          <div className="sheet-bar">
            <span className="mut small">{current.name} · {Math.round(current.size / 1024)} KB</span>
            <span className="spacer" />
            <a className="btn sm" href={fileHref(domain, screen.version, current.name)} download data-testid={`download-${tab}`}>Download {FILES[tab]?.label ?? tab}</a>
          </div>
        ) : null}
        {tab === "summary" ? <SummaryProse prose={prose} /> : null}
        {tab === "highlights" ? <HighlightsProse prose={prose} /> : null}
        {tab === "internal" ? <InternalProse prose={prose} meta={screen.meta as Record<string, unknown>} /> : null}
        {tab === "people" || tab === "jobs" || tab === "stack" ? <Counts screen={screen} /> : null}
        {tables.map((t: ParityTableRef) => <ParityTable key={t.id} domain={domain} version={screen.version} table={t} />)}
      </div>
    </section>
  );
}
