// A deep-dive table of an account (People, Signals, Job postings, Tech stack and teams, News,
// From your mail and drive, Sources, Platform picture by lane, Coverage), sorted, filtered and
// paged on the server. Once files are delivered, People, Job postings, Tech stack, the Platform
// picture and Coverage are the delivered files' own tables (their columns, order and cells), and
// People, Job postings and Tech stack show every sheet of their workbook as a tab (?tab=).
import type { Metadata } from "next";
import Link from "next/link";
import type { Sheet, SheetTab } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { SheetView } from "@/components/accounts/SheetView";
import { SheetTabs } from "@/components/accounts/SheetTabs";
import { TabView } from "@/components/accounts/TabView";

export const dynamic = "force-dynamic";

const TITLES: Record<string, string> = {
  people: "People", signals: "Signals", jobs: "Job postings", technology: "Tech stack and teams",
  news: "News", "mail-and-drive": "From your mail and drive", sources: "Sources", searches: "Web searches",
  facts: "Platform picture by lane", coverage: "Coverage",
};
const PAGE = 200;
const MAX_LIMIT = 500;

type Props = { params: Promise<{ domain: string; sheet: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> };

/** A path segment as text: a malformed escape ("%zz") is kept as typed instead of crashing. */
function segment(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

/** One query value: a repeated parameter (?sort=a&sort=b) counts its first value. */
function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { sheet } = await params;
  return { title: `${TITLES[segment(sheet)] ?? "Table"} · Account Intelligence` };
}

/** The delivered file itself, as the client downloads it from the Files card. */
function fileHref(domain: string, id: string | null | undefined): string | null {
  return id ? `/v1/accounts/${encodeURIComponent(domain)}/deliverables/download?${new URLSearchParams({ ids: id }).toString()}` : null;
}

function Failed({ back, base, failed, bad }: { back: string; base: string; failed: boolean; bad: boolean }) {
  return (
    <div className="pg tight gap-18">
      <Link className="back" href={back} prefetch={false}>‹ Back to the account</Link>
      {failed ? <StateCard title="This table could not be loaded" body="Reload the page in a moment." action={<a className="btn" href={base}>Reload</a>} />
        : bad ? <StateCard title="That filter or sort is not available" body="Open the table with its default view." action={<Link className="btn" href={base}>Open the table</Link>} />
        : <StateCard title="Table not found" body="It is not in your workspace." action={<Link className="btn" href={back}>Back to the account</Link>} />}
    </div>
  );
}

export default async function SheetPage({ params, searchParams }: Props) {
  const { domain: raw, sheet: rawKey } = await params;
  const domain = segment(raw);
  const key = segment(rawKey);
  const query = await searchParams;
  const sp = {
    tab: one(query.tab), limit: one(query.limit), offset: one(query.offset),
    filter: one(query.filter), sort: one(query.sort), dir: one(query.dir),
  };
  const base = `/accounts/${encodeURIComponent(domain)}/${encodeURIComponent(key)}`;
  const back = `/accounts/${encodeURIComponent(domain)}`;
  const api = `/v1/accounts/${encodeURIComponent(domain)}/sheets/${encodeURIComponent(key)}`;

  // a sheet of the delivered workbook other than the main table
  if (sp.tab && /^[a-z0-9-]{1,48}$/.test(sp.tab)) {
    let tab: SheetTab | null = null;
    let failed = false;
    try {
      tab = await serverGet<SheetTab>(`${api}/tabs/${encodeURIComponent(sp.tab)}`, `${base}?tab=${sp.tab}`);
    } catch (e) {
      if (!(e instanceof ServerApiError)) throw e;
      failed = true;
    }
    if (!tab) return <Failed back={back} base={base} failed={failed} bad={false} />;
    const download = fileHref(domain, tab.file_id);
    const tabs = tab.tabs ?? [];
    return (
      <div className="pg tight gap-18">
        <div className="head">
          <div className="ttl">
            <Link className="back" href={back} prefetch={false}>‹ {tab.account_name}</Link>
            <h1>{tab.title}</h1>
            <div className="sub">The {tab.tab_label} sheet of your delivered file, as the file prints it.</div>
          </div>
          {download ? <a className="btn sm" href={download} download>Download the file</a> : null}
        </div>
        <SheetTabs tabs={tabs} base={base} current={tab.tab ?? sp.tab} />
        <TabView tab={tab} />
      </div>
    );
  }

  const limit = Math.min(MAX_LIMIT, Math.max(PAGE, Number(sp.limit) || PAGE));
  const offset = Math.max(0, Math.floor(Number(sp.offset) || 0));
  const q = new URLSearchParams({ limit: String(limit) });
  if (offset) q.set("offset", String(offset));
  for (const k of ["filter", "sort", "dir"] as const) if (sp[k]) q.set(k, sp[k]!);
  let sheet: Sheet | null = null;
  let failed = false;
  let bad = false;
  try {
    sheet = await serverGet<Sheet>(`${api}?${q.toString()}`, base, [422]);
    if (sheet === null && (sp.filter || sp.sort || sp.dir)) bad = true;
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  if (!sheet) return <Failed back={back} base={base} failed={failed} bad={bad} />;
  const rows = sheet.rows ?? [];
  const start = Math.max(0, sheet.offset ?? offset);
  const shown = start + rows.length;
  const view = { filter: sheet.filter, sort: sheet.sort, dir: sheet.dir };
  const at = (o: number, l: number) => `${base}?${new URLSearchParams({ ...view, limit: String(l), ...(o ? { offset: String(o) } : {}) }).toString()}`;
  // "Show more rows" grows the page to 500 rows; past that the table pages by 500
  const more = shown < sheet.total && limit < MAX_LIMIT && offset === 0 ? at(0, Math.min(MAX_LIMIT, limit + PAGE)) : null;
  const next = shown < sheet.total && !more ? at(offset + limit, limit) : null;
  const prev = offset > 0 ? at(Math.max(0, offset - limit), limit) : null;
  // a page past the last row (an old link after rows were removed) offers the first page
  const past = offset > 0 && rows.length === 0 ? at(0, limit) : null;
  const download = fileHref(domain, sheet.file_id);
  return (
    <div className="pg tight gap-18">
      <div className="head">
        <div className="ttl">
          <Link className="back" href={back} prefetch={false}>‹ {sheet.account_name}</Link>
          <h1>{sheet.title}</h1>
          <div className="sub">{sheet.scope}</div>
        </div>
        {sheet.private ? <span className="pill internal big">Private to you</span> : null}
        {download ? <a className="btn sm" href={download} download>Download the file</a> : null}
      </div>
      <SheetTabs tabs={sheet.tabs ?? []} base={base} current={null} />
      <SheetView sheet={sheet} domain={domain} base={base} moreHref={more} />
      {prev || next ? (
        <nav className="pager" aria-label="Pages of rows">
          {prev ? <Link className="btn sm" href={past ?? prev} prefetch={false}>{past ? "‹ First rows" : "‹ Previous rows"}</Link> : null}
          {rows.length ? <span className="fine">Rows {start + 1} to {shown} of {sheet.total}</span> : null}
          {next ? <Link className="btn sm" href={next} prefetch={false}>Next rows ›</Link> : null}
        </nav>
      ) : null}
      <div className="fine">{sheet.footnote}</div>
    </div>
  );
}
