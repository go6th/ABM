// The archive of an account, shown like a web archive: an archived copy of an address the
// research stored (the page the seller reads when the original has been taken down), and the
// index of everything archived. The copy's layout follows its kind: a job posting (fields,
// sentences, description), a news article (headline, publisher, date, summary, text), a profile,
// a technology detection, or any other page (title and text). Server-rendered and pure: every
// value is text React escapes, the original address opens only through safeLink, and nothing
// here sets an inline style (the look is the arc-* classes of ui/components.css).
import Link from "next/link";
import type { ArchiveList, ArchivedCopy } from "@/lib/gen/api.gen";
import { LINK_REL, safeLink } from "@/lib/render";
import { Grade, Pill, StateCard } from "@/ui/design";

const DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
/** A copy older than this many days carries the quiet "may have changed" note. */
export const OLD_AFTER_DAYS = 90;

const KIND: Record<string, string> = {
  posting: "Job posting", profile: "Profile", page: "Page", feed_item: "Feed item", detection: "Technology detection",
};
const SCHEMA_KIND: Record<string, string> = {
  "posting-archive": "posting", "news-archive": "news", "profile-archive": "profile", "detection-archive": "detection", "page-archive": "page",
};
const STATUS: Record<string, string> = { open: "Open", closed: "Closed", unknown: "Not known" };

type Rec = Record<string, unknown>;

function str(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

function recordOf(copy: ArchivedCopy): Rec | null {
  const r = copy.record;
  return r && typeof r === "object" && !Array.isArray(r) ? (r as Rec) : null;
}

/** The layout a copy gets: its structured record's schema, else its kind of link. */
export function layoutOf(kind: string, schema: string | null | undefined): "posting" | "news" | "profile" | "detection" | "page" {
  const named = SCHEMA_KIND[str(schema).split("/")[0] ?? ""];
  const k = named ?? (kind === "feed_item" ? "page" : kind);
  return k === "posting" || k === "news" || k === "profile" || k === "detection" ? k : "page";
}

/** What the index calls a kind of copy: a news article is stored as a page but named by its schema. */
export function kindLabel(kind: string, schema?: string | null): string {
  const layout = layoutOf(kind, schema);
  return layout === "news" ? "News article" : (KIND[layout] ?? "Page");
}

/** "2026-10-20" or a timestamp as "20 October 2026"; the text as given when it is not a date. */
export function dateText(value: unknown): string {
  const t = str(value);
  if (!t) return "";
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? t : DATE.format(d);
}

function words(v: unknown): string {
  const t = str(v).replace(/[_-]+/g, " ");
  return t ? t[0]!.toUpperCase() + t.slice(1) : "";
}

/** What the capture said about the posting's status: "open", "closed", or "" when not given. */
export function statusAtCapture(record: Rec | null): string {
  const s = str(record?.status_at_capture).toLowerCase();
  return s in STATUS ? s : "";
}

function StatusPill({ status }: { status: string }) {
  if (!status) return null;
  return <Pill tone={status === "open" ? "ok" : status === "closed" ? "bad" : "neutral"}>{STATUS[status]}</Pill>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function daysSince(iso: string, now: Date): number {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? 0 : Math.floor((now.getTime() - d.getTime()) / 86_400_000);
}


/** A cleaned page or news copy (schema version 2): the article, tables, facts and provenance. */
export function isClean(record: Rec | null): boolean {
  return /\/2$/.test(str(record?.schema));
}

const NOT_GIVEN = new Set(["", "Not stated", "Not found"]);

function given(v: unknown): string {
  const t = str(v);
  return NOT_GIVEN.has(t) ? "" : t;
}

function list(v: unknown): Rec[] {
  return Array.isArray(v) ? (v as unknown[]).filter((x): x is Rec => !!x && typeof x === "object" && !Array.isArray(x)) : [];
}

function countWords(t: string): number {
  return t.split(/\s+/).filter(Boolean).length;
}

/** The article as headings, paragraphs and lists (the cleaned text has "## " headings and "- " items). */
function ArticleBody({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/).map((b) => b.trim()).filter(Boolean);
  return (
    <div className="arc-article">
      {blocks.map((b, i) => {
        if (b.startsWith("## ")) return <h3 key={i}>{b.slice(3)}</h3>;
        const lines = b.split("\n");
        if (lines.every((l) => /^(?:[-*•]|\d{1,2}[.)])\s+/.test(l))) {
          return <ul key={i}>{lines.map((l, j) => <li key={j}>{l.replace(/^(?:[-*•]|\d{1,2}[.)])\s+/, "")}</li>)}</ul>;
        }
        return <p key={i}>{b}</p>;
      })}
    </div>
  );
}

function SourceTable({ table }: { table: Rec }) {
  const columns = Array.isArray(table.columns) ? (table.columns as unknown[]).map(str) : [];
  const rows = Array.isArray(table.rows) ? (table.rows as unknown[]).map((r) => (Array.isArray(r) ? (r as unknown[]).map(str) : [])) : [];
  const title = str(table.title);
  return (
    <section className="card pad stack-10" aria-label={title || "Table from the source"}>
      {title ? <h2>{title}</h2> : null}
      <div className="table-wrap">
        <table className="table">
          <thead><tr>{columns.map((c, i) => <th key={i} scope="col">{c}</th>)}</tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i}>{columns.map((_, j) => <td key={j}>{r[j] ?? ""}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </section>
  );
}

/** The cleaned article of a page or news copy, in the order the archive shows it: key facts,
 * summary, people changes, source tables, article text, commentary, disclaimer, provenance. */
function CleanArticle({ record, copy }: { record: Rec; copy: ArchivedCopy }) {
  const events = list(record.events);
  const people = list(record.people);
  const tables = list(record.tables);
  const analysis = list(record.analysis);
  const body = copy.text;
  const long = countWords(body) > 300;
  const disclaimer = str(record.disclaimer);
  const duplicate = given(record.duplicate_of);
  const corroborates = Array.isArray(record.corroborates) ? (record.corroborates as unknown[]).map(str).filter(Boolean) : [];
  const quality = (record.quality && typeof record.quality === "object" ? record.quality : {}) as Rec;
  const flags = Array.isArray(quality.flags) ? (quality.flags as unknown[]).map(str).filter(Boolean) : [];
  const textBlock = body ? <ArticleBody text={body} /> : <p className="mut">No article text is kept for this copy.</p>;
  return (
    <>
      {events.length ? (
        <ul className="arc-facts" aria-label="Key facts">
          {events.slice(0, 4).map((e, i) => (
            <li key={i}>
              <b>{str(e.event_type)}</b>
              <span>{str(e.summary)}</span>
              {given(e.effective_date) ? <small>effective {dateText(e.effective_date)}</small> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {str(record.summary) ? <p className="arc-lead">{str(record.summary)}</p> : null}
      {people.length ? (
        <section className="card pad stack-10" aria-labelledby="arc-people">
          <h2 id="arc-people">People changes</h2>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Name</th><th scope="col">New role</th><th scope="col">Previous role</th><th scope="col">Change</th><th scope="col">Effective</th></tr></thead>
              <tbody>
                {people.map((p, i) => (
                  <tr key={i}>
                    <td>{str(p.name)}</td><td>{str(p.new_role)}</td><td>{str(p.previous_role)}</td>
                    <td>{str(p.change_type)}</td><td className="nowrap">{dateText(p.effective_date) || "Not stated"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
      {tables.map((t, i) => <SourceTable key={i} table={t} />)}
      <section className="card pad stack-10" aria-labelledby="arc-text">
        <h2 id="arc-text">Article text</h2>
        {long ? (
          <details className="arc-more">
            <summary>Show the full article ({countWords(body)} words)</summary>
            {textBlock}
          </details>
        ) : textBlock}
        {Array.isArray(record.withheld) && record.withheld.length ? <p className="fine">Some text was left out of this copy.</p> : null}
      </section>
      {analysis.length ? (
        <aside className="arc-comm" aria-label="Commentary">
          <b>Third-party / AI commentary — not a fact</b>
          {analysis.map((a, i) => <p key={i}><span className="arc-chip">{str(a.author_type) || "Analyst"}</span> {str(a.text)}</p>)}
        </aside>
      ) : null}
      {disclaimer ? <p className="fine">{disclaimer}</p> : null}
      <footer className="arc-prov" aria-label="Provenance">
        <dl>
          <div><dt>URL</dt><dd>{copy.url}</dd></div>
          <div><dt>Collected</dt><dd>{dateText(record.collected_at) || dateText(copy.archived_at)}</dd></div>
          {str(record.content_hash) ? <div><dt>Content hash</dt><dd>{str(record.content_hash)}</dd></div> : null}
          {duplicate ? <div><dt>Duplicate of</dt><dd>{duplicate}</dd></div> : null}
          {corroborates.length ? <div><dt>Corroborates</dt><dd>{corroborates.join(", ")}</dd></div> : null}
          <div><dt>Cleaning</dt><dd>{Number(quality.boilerplate_removed ?? 0)} page-chrome lines, {Number(quality.duplicates_removed ?? 0)} repeats removed; {Number(quality.tables_repaired ?? 0)} tables repaired</dd></div>
          {flags.length ? <div><dt>Flagged</dt><dd>{flags.join("; ")}</dd></div> : null}
        </dl>
        <p className="fine">The page as pulled is kept for audit and is not shown here.</p>
      </footer>
    </>
  );
}

type Step = { label: string; date: string };

/** One archived copy, Wayback-style: the capture strip, the quiet note, the head, the fields and
 * the text with its line breaks. */
export function ArchivedCopyView({ copy, domain, now = new Date() }: { copy: ArchivedCopy; domain: string; now?: Date }) {
  const record = recordOf(copy);
  const layout = layoutOf(copy.kind, str(record?.schema));
  const status = layout === "posting" ? statusAtCapture(record) : "";
  const original = safeLink(copy.url);
  const captured = dateText(copy.archived_at);
  const age = daysSince(copy.archived_at, now);
  const here = `/accounts/${encodeURIComponent(domain)}`;
  const download = `/v1/accounts/${encodeURIComponent(domain)}/archive?${new URLSearchParams({ url: copy.url, download: "true" }).toString()}`;
  const back = layout === "posting" ? { href: `${here}/jobs`, label: "Back to Job Postings" }
    : layout === "news" ? { href: `${here}/news`, label: "Back to News" }
    : { href: `${here}/archive`, label: "Back to the archive" };
  const withheld = Array.isArray(record?.withheld) ? record!.withheld : [];
  const platforms = Array.isArray(record?.platforms) ? (record!.platforms as unknown[]).map(str).filter(Boolean) : [];
  const sentences = Array.isArray(record?.platform_sentences) ? (record!.platform_sentences as Rec[]) : [];

  // the head: what the copy is called, and the line under it
  const title = layout === "profile" ? str(record?.name) || copy.title || copy.url
    : layout === "detection" ? str(record?.technology) || copy.title || copy.url
    : str(record?.title) || copy.title || copy.url;
  let sub = "";
  if (layout === "posting") sub = hostOf(copy.url) ? `Job posting on ${hostOf(copy.url)}` : "Job posting";
  else if (layout === "news") sub = [str(record?.publisher), isClean(record) ? dateText(given(record?.published_date)) : "", words(record?.category)].filter(Boolean).join(" · ");
  else if (layout === "profile") sub = str(record?.job_title);
  else if (layout === "page") sub = [str(record?.publisher), dateText(given(record?.published_date))].filter(Boolean).join(" · ");

  // the dates of the item as a short sequence (shown when there are at least two)
  const steps: Step[] = [];
  if (layout === "posting") {
    steps.push({ label: "First seen", date: dateText(record?.first_seen) }, { label: "Last seen", date: dateText(record?.last_seen) });
  } else if (layout === "news") {
    steps.push({ label: "Published", date: dateText(given(record?.published_date)) });
  } else if (layout === "detection") {
    steps.push({ label: "First seen", date: dateText(record?.first_seen) }, { label: "Last seen", date: dateText(record?.last_seen) });
  }
  const sequence = [...steps.filter((s) => s.date), { label: "Captured", date: captured }];
  const clean = (layout === "news" || layout === "page") && isClean(record);
  const typePill = layout === "news" ? <Pill tone="neutral">News article</Pill> : null;
  const derived = record?.derived_from && typeof record.derived_from === "object" ? (record.derived_from as Rec) : null;
  const primary = derived ? safeLink(given(derived.url)) : null;
  const body = copy.text;
  const textTitle = layout === "posting" && record ? "Full description" : layout === "news" ? "Article text" : "Stored text";

  return (
    <div className="pg tight gap-18 arc-page">
      <Link className="back" href={back.href} prefetch={false}>‹ {back.label}</Link>
      <section className="arc-strip" role="note" aria-label="Archived copy">
        <div className="arc-top">
          <b>Archived copy · captured {captured}</b>
          {status ? <Pill tone={status === "open" ? "ok" : status === "closed" ? "bad" : "neutral"}>{status === "open" ? "Open when captured" : status === "closed" ? "Closed when captured" : "Status not known"}</Pill> : typePill}
          <span className="grow" />
          {original ? <a className="btn sm" href={original} target="_blank" rel={LINK_REL}>Open original (may no longer exist)</a> : null}
        </div>
        {sequence.length >= 2 ? (
          <ol className="arc-steps" aria-label="Dates of this copy">
            {sequence.map((s, i) => (
              <li key={s.label} className={i === sequence.length - 1 ? "now" : undefined}><b>{s.label}</b><span>{s.date}</span></li>
            ))}
          </ol>
        ) : null}
        <div className="arc-orig">{copy.url}</div>
      </section>
      {status === "closed" ? (
        <p className="arc-note">This posting was closed when it was captured, so the original link may no longer work. What follows is what it said.</p>
      ) : age >= OLD_AFTER_DAYS ? (
        <p className="arc-note">This copy was captured {age} days ago. The original may have changed since.</p>
      ) : null}
      <div className="head">
        <div className="ttl">
          <h1>{title}</h1>
          {sub ? <div className="sub">{sub}</div> : null}
          {clean ? (
            <div className="arc-badges">
              {given(record?.source_type) ? <Pill tone={record?.is_ai_generated ? "warn" : "neutral"}>{str(record?.source_type)}</Pill> : null}
              {given(record?.fact_grade) ? <Grade grade={str(record?.fact_grade)} /> : null}
            </div>
          ) : null}
        </div>
        <div className="arc-actions">
          {clean && original ? <a className="btn sm" href={original} target="_blank" rel={LINK_REL}>Open original</a> : null}
          {clean && primary ? <a className="btn sm" href={primary} target="_blank" rel={LINK_REL}>Open primary source</a> : null}
          <a className="btn sm" href={download} download>Download JSON</a>
          <Link className="btn sm primary" href={back.href} prefetch={false}>{back.label}</Link>
        </div>
      </div>

      {layout === "posting" && record ? (
        <section className="card pad" aria-label="The posting when it was captured">
          <dl className="arc-fields">
            <Field label="Requisition">{str(record.requisition) || "Not given"}</Field>
            <Field label="Location">{str(record.location) || "Not given"}</Field>
            <Field label="Status when captured">{status ? <StatusPill status={status} /> : "Not known"}</Field>
            <Field label="Class">{str(record.class) || "Not given"}</Field>
            <Field label="First seen">{dateText(record.first_seen) || "Not given"}</Field>
            <Field label="Last seen">{dateText(record.last_seen) || "Not given"}</Field>
            <Field label="Seniority">{words(record.seniority) || "Not given"}</Field>
            <Field label="Platforms named">{platforms.length ? platforms.join(", ") : "None named"}</Field>
          </dl>
        </section>
      ) : null}
      {layout === "profile" && record ? (
        <section className="card pad" aria-label="The profile when it was captured">
          <dl className="arc-fields">
            <Field label="Name">{str(record.name) || "Not given"}</Field>
            <Field label="Title">{str(record.job_title) || "Not given"}</Field>
            <Field label="Headline">{str(record.headline) || "Not given"}</Field>
            <Field label="Location">{str(record.location) || "Not given"}</Field>
          </dl>
        </section>
      ) : null}
      {layout === "detection" && record ? (
        <section className="card pad" aria-label="The detection when it was captured">
          <dl className="arc-fields">
            <Field label="Technology">{str(record.technology) || "Not given"}</Field>
            <Field label="Sources">{typeof record.sources === "number" ? String(record.sources) : "Not given"}</Field>
            <Field label="First seen">{dateText(record.first_seen) || "Not given"}</Field>
            <Field label="Last seen">{dateText(record.last_seen) || "Not given"}</Field>
          </dl>
        </section>
      ) : null}
      {layout === "news" && !clean && str(record?.summary) ? <p className="arc-lead">{str(record?.summary)}</p> : null}
      {clean && record ? <CleanArticle record={record} copy={copy} /> : null}

      {sentences.length ? (
        <section className="card pad stack-10" aria-labelledby="arc-sentences">
          <h2 id="arc-sentences">Sentences that name a platform</h2>
          <ul className="arc-sent">
            {sentences.map((s, i) => (
              <li key={i}><span className="arc-chip">{str(s.platform)}</span><span>{str(s.text)}</span></li>
            ))}
          </ul>
        </section>
      ) : null}

      {layout === "profile" || layout === "detection" || clean ? null : (
        <section className="card pad stack-10" aria-labelledby="arc-text">
          <h2 id="arc-text">{textTitle}</h2>
          {body ? <div className="arc-text">{body}</div> : <p className="mut">No text is kept for this copy.</p>}
          {withheld.length ? <p className="fine">Some text was left out of this copy.</p> : null}
        </section>
      )}
      {(layout === "profile" || layout === "detection") && !record && body ? (
        <section className="card pad stack-10" aria-labelledby="arc-text">
          <h2 id="arc-text">Stored text</h2>
          <div className="arc-text">{body}</div>
        </section>
      ) : null}
      <p className="fine"><Link href={`${here}/archive`} prefetch={false}>All archived copies of this account</Link></p>
    </div>
  );
}

const KIND_FILTERS: Array<[string, string]> = [["", "All"], ["posting", "Job postings"], ["page", "Pages and news"]];

/** The index columns that filter, with the query parameter each sends (the API filters on them). */
export const FILTER_COLUMNS: Array<[string, string]> = [
  ["published", "Published"], ["title", "Title"], ["publisher", "Publisher"], ["source_type", "Source type"], ["grade", "Grade"],
  ["event_type", "Event types"], ["person", "People named"], ["lane", "Lane"], ["collected", "Collected"],
];

/** The index of the account's archived items: what was captured, when, its source type, grade, the
 * events and people it names; every column filters. View opens the copy, original opens the
 * address the research read. */
export function ArchiveIndexView({ list, domain, name, kind, showMore, filters = {} }: { list: ArchiveList; domain: string; name: string; kind: string; showMore: string | null; filters?: Record<string, string> }) {
  const here = `/accounts/${encodeURIComponent(domain)}`;
  const base = `${here}/archive`;
  const items = list.items ?? [];
  const active = Object.values(filters).some(Boolean);
  const keep = (k: string) => new URLSearchParams({ ...(k ? { kind: k } : {}), ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) }).toString();
  return (
    <div className="pg tight gap-18 arc-page">
      <div className="head">
        <div className="ttl">
          <Link className="back" href={here} prefetch={false}>‹ {name}</Link>
          <h1>Archive</h1>
          <div className="sub">A copy of every page and job posting the research stored, kept as it was captured. A posting that has since been taken down can still be read here.</div>
        </div>
      </div>
      <nav className="sheet-tabs" aria-label="Kinds of archived item">
        {KIND_FILTERS.map(([k, label]) => (
          <Link key={k || "all"} href={keep(k) ? `${base}?${keep(k)}` : base} aria-current={kind === k ? "page" : undefined} prefetch={false}>{label}</Link>
        ))}
      </nav>
      <form className="arc-filters" method="get" action={base} aria-label="Filter the archive">
        {kind ? <input type="hidden" name="kind" value={kind} /> : null}
        {FILTER_COLUMNS.map(([key, label]) => (
          <label key={key}>
            <span>{label}</span>
            <input type="search" name={key} defaultValue={filters[key] ?? ""} maxLength={100} />
          </label>
        ))}
        <div className="arc-filter-actions">
          <button className="btn sm primary" type="submit">Filter</button>
          {active ? <Link className="btn sm" href={kind ? `${base}?kind=${kind}` : base} prefetch={false}>Clear</Link> : null}
        </div>
      </form>
      {items.length === 0 ? (
        active
          ? <StateCard title="No archived item matches" body="Change or clear a filter." action={<Link className="btn" href={kind ? `${base}?kind=${kind}` : base}>Clear filters</Link>} />
          : <StateCard title="Nothing archived yet" body="Copies appear here once a research has stored pages or job postings for this account." />
      ) : (
        <div className="table-wrap">
          <table className="table arc-index">
            <caption className="sr-only">Archived items, newest capture first, {items.length} of {list.total}.</caption>
            <thead>
              <tr>
                <th scope="col">Title</th>
                <th scope="col">Published</th>
                <th scope="col">Publisher</th>
                <th scope="col">Source type</th>
                <th scope="col">Grade</th>
                <th scope="col">Event types</th>
                <th scope="col">People named</th>
                <th scope="col">Lane</th>
                <th scope="col">Collected</th>
                <th scope="col">Links</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const layout = layoutOf(it.kind, it.record_schema);
                const status = layout === "posting" ? (it.status_at_capture ?? "").toLowerCase() : "";
                const original = safeLink(it.url);
                const view = `${base}?${new URLSearchParams({ url: it.url }).toString()}`;
                return (
                  <tr key={`${it.url}|${it.archived_at}`}>
                    <td className="arc-title">
                      <Link className="arc-open" href={view} prefetch={false}>{it.title || it.url}</Link>
                      {it.requisition ? <small>{it.requisition}</small> : null}
                      {layout === "posting" ? <small>{[it.location, STATUS[status]].filter(Boolean).join(" · ")}</small> : null}
                    </td>
                    <td className="nowrap">{dateText(it.published_date && !NOT_GIVEN.has(it.published_date) ? it.published_date : "")}</td>
                    <td>{it.publisher ?? ""}</td>
                    <td>{it.source_type ?? kindLabel(it.kind, it.record_schema)}</td>
                    <td>{it.fact_grade ? <Grade grade={it.fact_grade} /> : null}</td>
                    <td>{it.event_types ?? ""}</td>
                    <td>{it.people ?? ""}</td>
                    <td>{it.lanes ?? ""}</td>
                    <td className="nowrap">{dateText(it.archived_at)}</td>
                    <td className="nowrap">
                      <Link className="src-link" href={view} prefetch={false}>View</Link>
                      {original ? <a className="arc-link" href={original} target="_blank" rel={LINK_REL}>original</a> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="row between">
        <span className="count">Showing {items.length} of {list.total}</span>
        {showMore ? <Link className="btn sm" href={showMore} prefetch={false}>Show more</Link> : null}
      </div>
    </div>
  );
}
