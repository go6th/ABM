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
import { Pill, StateCard } from "@/ui/design";

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
  else if (layout === "news") sub = [str(record?.publisher), words(record?.category)].filter(Boolean).join(" · ");
  else if (layout === "profile") sub = str(record?.job_title);
  else if (layout === "page") sub = [str(record?.publisher), dateText(record?.published_date)].filter(Boolean).join(" · ");

  // the dates of the item as a short sequence (shown when there are at least two)
  const steps: Step[] = [];
  if (layout === "posting") {
    steps.push({ label: "First seen", date: dateText(record?.first_seen) }, { label: "Last seen", date: dateText(record?.last_seen) });
  } else if (layout === "news") {
    steps.push({ label: "Published", date: dateText(record?.published_date) });
  } else if (layout === "detection") {
    steps.push({ label: "First seen", date: dateText(record?.first_seen) }, { label: "Last seen", date: dateText(record?.last_seen) });
  }
  const sequence = [...steps.filter((s) => s.date), { label: "Captured", date: captured }];
  const typePill = layout === "news" ? <Pill tone="neutral">News article</Pill> : null;
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
        </div>
        <div className="arc-actions">
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
      {layout === "news" && str(record?.summary) ? <p className="arc-lead">{str(record?.summary)}</p> : null}

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

      {layout === "profile" || layout === "detection" ? null : (
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

/** The index of the account's archived items: what was captured, when, and the postings' status
 * when captured; View opens the copy, original opens the address the research read. */
export function ArchiveIndexView({ list, domain, name, kind, showMore }: { list: ArchiveList; domain: string; name: string; kind: string; showMore: string | null }) {
  const here = `/accounts/${encodeURIComponent(domain)}`;
  const base = `${here}/archive`;
  const items = list.items ?? [];
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
          <Link key={k || "all"} href={k ? `${base}?kind=${k}` : base} aria-current={kind === k ? "page" : undefined} prefetch={false}>{label}</Link>
        ))}
      </nav>
      {items.length === 0 ? (
        <StateCard title="Nothing archived yet" body="Copies appear here once a research has stored pages or job postings for this account." />
      ) : (
        <div className="table-wrap">
          <table className="table arc-index">
            <caption className="sr-only">Archived items, newest capture first, {items.length} of {list.total}.</caption>
            <thead>
              <tr>
                <th scope="col">Title</th>
                <th scope="col">Kind</th>
                <th scope="col">Captured</th>
                <th scope="col">Status when captured</th>
                <th scope="col">Location</th>
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
                    <td className="arc-title"><Link className="arc-open" href={view} prefetch={false}>{it.title || it.url}</Link>{it.requisition ? <small>{it.requisition}</small> : null}</td>
                    <td>{kindLabel(it.kind, it.record_schema)}</td>
                    <td className="nowrap">{dateText(it.archived_at)}</td>
                    <td><StatusPill status={status in STATUS ? status : ""} /></td>
                    <td>{it.location ?? ""}</td>
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
