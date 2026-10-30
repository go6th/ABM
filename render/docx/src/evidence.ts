// The Sources and Evidence file, a port of the reference's build_evidence.js: Parts A to I in the
// same order with the same table widths. Every paragraph that names the account, a date, a count
// or a source ID comes from the bundle.
import type { Document } from "docx";
import { cite, fileNames, meta, type AccountBundle } from "./bundle.js";
import { bullet, h1, h2, link, makeDoc, p, table, title, type Block, type CellContent } from "./lib.js";
import { cl } from "./text.js";
import { jobsCountRows } from "./summary.js";

const hl = (text: string, url: string) => link(cl(text), url);
const clRow = (r: readonly string[]): string[] => r.map(cl);

export function buildEvidence(b: AccountBundle): Document {
  const m = meta(b);
  const A = m.prefix;
  const E = b.prose.evidence;
  const ids = b.meta.source_ids ?? {};
  const ch: Block[] = [];

  ch.push(...title(`${A} (${m.legal}): Sources and Evidence`,
    `Research date ${m.runDate}. Companion to ${fileNames(A).summary}. Every source ID and job ID cited in the Summary is listed here with its link. ${m.byline}`));

  ch.push(h1("Part A. Source register"));
  ch.push(table([700, 3800, 1300, 1300, 2000, 3800], ["ID", "Source", "Type", "Date", "How retrieved", "What it supports"],
    Object.entries(b.sources).map(([id, s]) => [id, hl(s.name, s.url), cl(s.type), cl(s.date), cl(s.how), cl(s.supports)])));

  ch.push(h1("Part B. Job posting extracts for postings read in full", true));
  if (E.part_b_intro) ch.push(p(E.part_b_intro));
  ch.push(table([700, 3200, 1300, 1500, 4800, 1300], ["ID", "Posting", "Platforms", "Class", "What the posting says", "Entity"],
    Object.entries(b.jobs_read).map(([id, j]) => [id, hl(j.name, j.url), cl(j.plats), j.cls, cl(j.what), cl(j.ent)])));

  ch.push(h1("Part C. Postings read one by one, by lane", true));
  for (const lane of b.meta.part_c_lanes ?? b.meta.lanes) {
    const rows = Object.entries(b.jobs_read).filter(([, j]) => j.plats.split(", ").includes(lane));
    const counts = b.computed.jobs_counts[lane] ?? { total: 0, open: 0 };
    ch.push(h2(`${lane}: ${rows.length} postings read in full (${counts.total} ${A} postings name it, ${counts.open} open on ${m.pullDateShort})`));
    if (rows.length) {
      const body: CellContent[][] = rows.map(([id, j]) => [id, hl(j.name, j.url), j.cls, j.status, cl(j.what.split(": ").slice(1).join(": ") || j.what)]);
      ch.push(table([700, 4200, 1800, 1200, 5000], ["ID", "Posting", "Class", "Status", "Classification reasoning"], body));
    } else {
      ch.push(p("No build or owner role for this platform in the 12 month window; every match is a working tool or user role mention, read by platform sentence and recorded in the Job Postings workbook."));
    }
    ch.push(p(""));
  }
  if (E.part_c_closing) ch.push(p(cl(E.part_c_closing)));

  ch.push(h1("Part D. Video, article and release extracts", true));
  for (const x of E.extracts) {
    ch.push(h2(`${cl(x.title)} (${x.id})`));
    ch.push(hl(x.url, x.url));
    for (const q of x.quotes) ch.push(bullet(cl(q)));
  }

  ch.push(h1("Part E. Job board captures", true));
  if (E.board_paragraph) ch.push(p(cl(E.board_paragraph)));
  ch.push(h2(`Live postings captured on ${m.pullDate} that name a lane platform`));
  ch.push(table([5200, 2000, 1500, 1500, 1500], ["Posting", "Platforms named", "Entity", "Read in full", "Class"],
    E.live_rows.map((r) => [hl(r[0] ?? "", r[1] ?? ""), cl(r[2]), cl(r[3]), r[4] ?? "", r[5] ?? ""])));

  ch.push(h1("Part F. Leads not verified, and items checked and dropped", true));
  ch.push(table([3600, 4200, 1400, 2500], ["Lead", "Queries and checks tried", "Status", "Source"], E.leads.map(clRow)));

  ch.push(h1("Part G. How to keep copies of the original pages", true));
  if (E.part_g) ch.push(p(E.part_g));

  ch.push(h1("Part H. Data pulls", true));
  ch.push(h2(`Technology detections for lane platforms${cite(ids.detections)}`));
  if (E.detections_note) ch.push(p(E.detections_note));
  ch.push(table([3400, 1400, 1400, 1000, 1500, 1500], ["Technology", "First seen", "Last seen", "Score", "Seen on postings", "Seen on pages"],
    E.detections.map((r) => r.map((x) => cl(String(x))))));
  ch.push(p(""));
  ch.push(h2(`Postings naming each lane platform, ${A} positions, 12 months plus live captures${cite(ids.feed)}`));
  ch.push(table([1800, 1400, 1200, 1200, 1800, 1400, 1400], ["Platform", "Postings", "Open", "Build", "Admin and owner", "Working tool", "User role"], jobsCountRows(b)));
  ch.push(p(""));
  ch.push(h2("Entity split of the 12 month postings"));
  ch.push(table([6000, 2000], ["Entity", "Postings"], Object.entries(b.computed.ent_counts).map(([k, v]) => [cl(k), String(v)])));
  ch.push(p(""));
  ch.push(h2(E.news_events_heading ?? `News feed events${cite(ids.news_feed)}, newest first, ${E.news_events.length} events; feed only unless the Summary table says Read`));
  ch.push(table([1300, 2000, 5200, 2700], ["Date", "Category", "Headline as the feed wrote it", "Most relevant source domain"], E.news_events.map(clRow)));
  ch.push(p(""));
  ch.push(h2(`Platform teams${cite(ids.teams)}`));
  if (E.teams_note) ch.push(p(E.teams_note));

  ch.push(h1("Part I. Full URLs written out", true));
  for (const [id, s] of Object.entries(b.sources)) ch.push(p(`${id}: ${s.url}`, { size: 16 }));
  for (const [id, j] of Object.entries(b.jobs_read)) ch.push(p(`${id}: ${j.url}`, { size: 16 }));

  return makeDoc(ch, true);
}
