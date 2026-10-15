// A deep-dive table of an account (People, Signals, Job postings, Tech stack and teams, News,
// From your mail and drive, Sources, Coverage), sorted, filtered and paged on the server.
import type { Metadata } from "next";
import Link from "next/link";
import type { Sheet } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { SheetView } from "@/components/accounts/SheetView";

export const dynamic = "force-dynamic";

const TITLES: Record<string, string> = {
  people: "People", signals: "Signals", jobs: "Job postings", technology: "Tech stack and teams",
  news: "News", "mail-and-drive": "From your mail and drive", sources: "Sources", searches: "Web searches", coverage: "Coverage",
};

type Props = { params: Promise<{ domain: string; sheet: string }>; searchParams: Promise<Record<string, string | undefined>> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { sheet } = await params;
  return { title: `${TITLES[sheet] ?? "Table"} · Account Intelligence` };
}

export default async function SheetPage({ params, searchParams }: Props) {
  const { domain: raw, sheet: key } = await params;
  const domain = decodeURIComponent(raw);
  const sp = await searchParams;
  const base = `/accounts/${encodeURIComponent(domain)}/${encodeURIComponent(key)}`;
  const limit = Math.min(500, Math.max(200, Number(sp.limit) || 200));
  const q = new URLSearchParams({ limit: String(limit) });
  for (const k of ["filter", "sort", "dir"] as const) if (sp[k]) q.set(k, sp[k]!);
  let sheet: Sheet | null = null;
  let failed = false;
  let bad = false;
  try {
    sheet = await serverGet<Sheet>(`/v1/accounts/${encodeURIComponent(domain)}/sheets/${encodeURIComponent(key)}?${q.toString()}`, base, [422]);
    if (sheet === null && (sp.filter || sp.sort || sp.dir)) bad = true;
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  const back = `/accounts/${encodeURIComponent(domain)}`;
  if (!sheet) {
    return (
      <div className="pg tight gap-18">
        <Link className="back" href={back} prefetch={false}>‹ Back to the account</Link>
        {failed ? <StateCard title="This table could not be loaded" body="Reload the page in a moment." action={<a className="btn" href={base}>Reload</a>} />
          : bad ? <StateCard title="That filter or sort is not available" body="Open the table with its default view." action={<Link className="btn" href={base}>Open the table</Link>} />
          : <StateCard title="Table not found" body="It is not in your workspace." action={<Link className="btn" href={back}>Back to the account</Link>} />}
      </div>
    );
  }
  const shown = sheet.offset + sheet.rows.length;
  const more = shown < sheet.total && limit < 500
    ? `${base}?${new URLSearchParams({ filter: sheet.filter, sort: sheet.sort, dir: sheet.dir, limit: String(Math.min(500, limit + 200)) }).toString()}`
    : null;
  return (
    <div className="pg tight gap-18">
      <div className="head">
        <div className="ttl">
          <Link className="back" href={back} prefetch={false}>‹ {sheet.account_name}</Link>
          <h1>{sheet.title}</h1>
          <div className="sub">{sheet.scope}</div>
        </div>
        {sheet.private ? <span className="pill internal big">Private to you</span> : null}
      </div>
      <SheetView sheet={sheet} base={base} moreHref={more} />
      {shown < sheet.total && !more ? <div className="fine">Showing the first {shown} of {sheet.total} rows. Open in Excel for all of them.</div> : null}
      <div className="fine">{sheet.footnote}</div>
    </div>
  );
}
