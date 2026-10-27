// The Account Summary, a port of the reference's build_summary.js: sections 0 to 10 in the same
// order, with the same page breaks and table widths. Every title, heading with a date or a count,
// paragraph, bullet and row comes from the bundle; the only words of its own this file carries
// are the neutral headings and the grade legend, which say nothing about any account.
import { ExternalHyperlink, Paragraph, TextRun, type Document } from "docx";
import { cite, fileNames, meta, type AccountBundle } from "./bundle.js";
import { FONT, bullet, h1, h2, labeled, link, makeDoc, num, p, run, table, title, type Block, type CellContent } from "./lib.js";
import { cl, joinAnd } from "./text.js";

const clRow = (r: readonly string[]): string[] => r.map(cl);

// The sentence under the section 5 hiring heading: the account's own postings after the other
// entities' are removed, and how many name a lane platform. Composed from the counts unless the
// bundle carries the whole paragraph.
function hiringBase(b: AccountBundle, prefix: string): string {
  if (b.prose.hiring_base) return b.prose.hiring_base;
  const counts = b.computed.ent_counts;
  const own = b.meta.entities.filter((e) => e.counted_as_account).reduce((n, e) => n + (counts[e.label] ?? 0), 0);
  const removed = b.meta.entities.filter((e) => !e.counted_as_account).map((e) => `${counts[e.label] ?? 0} ${e.phrase ?? `${e.label} positions`}`);
  const removedText = removed.length ? ` after removing ${joinAnd(removed)}` : "";
  return `Base: ${own} ${prefix} postings in the feed${removedText}; ${b.computed.lane_rows_account} of them name at least one lane platform${cite(b.meta.source_ids?.feed)}. `
    + "Build role means the platform is the job; Admin and owner role runs or governs it; Working tool means daily use; User role is a tag or boilerplate mention.";
}

// The pass 2 tracker rows sorted High, Medium, Optional, and within a priority the rows that need
// nothing first.
function sortTracker(rows: readonly (readonly string[])[]): (readonly string[])[] {
  const order: Record<string, number> = { High: 0, Medium: 1, Optional: 2 };
  const needs0 = (r: readonly string[]): number => (r[3] === "Nothing" ? 0 : 1);
  return [...rows].sort((a, b) => ((order[a[2] ?? ""] ?? NaN) - (order[b[2] ?? ""] ?? NaN)) || (needs0(a) - needs0(b)));
}

function hostOf(url: string): string {
  return new URL(url).hostname.replace(/^www\./, "");
}

export function jobsCountRows(b: AccountBundle): string[][] {
  return Object.entries(b.computed.jobs_counts).map(([k, v]) => [k, String(v.total), String(v.open), String(v.build), String(v.admin), String(v.tool), String(v.user)]);
}

export function buildSummary(b: AccountBundle): Document {
  const m = meta(b);
  const A = m.prefix;
  const names = fileNames(A);
  const P = b.prose;
  const ch: Block[] = [];

  ch.push(...title(`${A} (${m.legal}): Account Summary`,
    `Research date ${m.runDate}. Pursuing client: ${m.client}. Evidence, postings and source extracts are in ${names.evidence}. People are in ${names.people}, postings in ${names.jobs}, stack and teams in ${names.stack}. ${m.byline}`));

  ch.push(h1("How to read this file"));
  ch.push(p("Every statement carries a source ID in brackets (S for a source, J for a job posting read). Every row in a table carries a grade:"));
  ch.push(table([2000, 5200, 2400], ["Grade", "Meaning", "Use in outreach"], [
    ["Confirmed", "Read in a primary source during this run", "May be stated"],
    ["Probable", "Inferred from one or two sources, reasoning given in the row", "Ask, do not state"],
    ["Hypothesis", "Our synthesis, no source states it", "Ask, do not state"],
    ["Lead", "Seen in a secondary source, not verified", "Do not use until checked"]]));
  ch.push(p(""));
  ch.push(p(P.glossary));

  ch.push(h1("1. The account in plain words", true));
  ch.push(h2("The account picture"));
  for (const t of P.narrative.picture) ch.push(p(cl(t)));
  ch.push(h2("What stands out"));
  for (const t of P.narrative.stands_out) ch.push(bullet(cl(t)));
  ch.push(h2("What it likely means for each lane (interpretation labeled)"));
  for (const [k, t] of P.narrative.lanes) ch.push(labeled(`${k}.`, cl(t)));
  ch.push(h2("How this could be wrong"));
  for (const t of P.narrative.wrong) ch.push(bullet(cl(t)));
  ch.push(h2("The simplest way to sharpen it"));
  for (const t of P.narrative.sharpen) ch.push(num(cl(t)));

  ch.push(h1("2. Recent news (newest first)", true));
  ch.push(table([1200, 5200, 1000, 1000, 3000], ["Date", "What happened", "Read depth", "Source", "Why it matters"], P.news.map(clRow)));
  ch.push(p(""));
  ch.push(p(cl(P.news_not_covered)));

  ch.push(h1("3. Company and transformation baseline", true));
  for (const t of P.baseline) ch.push(bullet(cl(t)));

  ch.push(h1("4. Signals", true));
  const tiers: [keyof typeof P.signals, string][] = [
    ["Tier 1", "Tier 1, budget proximate"], ["Tier 2", "Tier 2, one step removed"],
    ["Tier 3", "Tier 3, context and openers"], ["Context and history", "Context and history (older than the shelf life)"]];
  for (const [tier, heading] of tiers) {
    ch.push(h2(heading));
    ch.push(table([5400, 1200, 1600, 1300, 1900], ["Finding", "Grade", "Lanes", "Date", "Sources"], P.signals[tier].map(clRow)));
    ch.push(p(""));
  }

  ch.push(h1("5. Platform picture by lane", true));
  ch.push(table([2600, 4400, 1200, 3200], ["Item", "What we found", "Grade", "Reasoning and sources"], P.platform.map(clRow)));
  ch.push(p(""));
  ch.push(h2(`Platform hiring over 12 months (${A} positions only, first seen ${b.meta.window_display.from} to ${b.meta.window_display.to})`));
  ch.push(p(cl(hiringBase(b, A))));
  ch.push(table([1600, 1000, 1000, 1000, 1500, 1300, 1200],
    ["Platform", "Postings naming it", `Open on ${m.pullDayMonth}`, "Build roles", "Admin and owner roles", "Working tool", "User role"], jobsCountRows(b)));
  ch.push(p(""));
  ch.push(p(cl(P.hiring_interpretation)));

  ch.push(h1("6. People", true));
  ch.push(p(cl(P.people_text.built)));
  ch.push(p(cl(P.people_text.exclusions)));
  ch.push(h2("Key people (priority A and the executives the lanes report to)"));
  const kp: CellContent[][] = P.key_people.map((r) => [
    cl(r[0]), cl(r[1]), cl(r[2]), r[3] ?? "",
    r[4] ? link("Profile", r[4]) : "No link found",
    cl(r[5])]);
  ch.push(table([1900, 3200, 1700, 800, 900, 2900], ["Name", "Title as reported", "Lane", "Priority", "Link", "Note (from the profile, the leadership page or a release)"], kp));
  ch.push(p(""));
  ch.push(p(cl(`The full list, with level, lane, priority, former consulting employers and links for all ${b.computed.people_total} people, is in ${names.people}.`)));
  ch.push(h2("Findings from profiles"));
  ch.push(p(cl(P.people_text.profiles)));
  ch.push(table([1700, 4200, 1200, 4300], ["Topic", "What the profiles show", "Grade", "Our interpretation"], P.profile_findings.map(clRow)));
  ch.push(p(""));
  ch.push(h2("People whose status changed"));
  ch.push(table([2200, 3200, 4300, 1700], ["Person", "Was", "Now", "Source"], P.status_changes.map(clRow)));
  ch.push(p(""));
  ch.push(h2("Interpretation"));
  for (const t of P.people_interpretation) ch.push(bullet(cl(t)));

  ch.push(h1("7. Hypotheses to test", true));
  for (const [h, q] of P.hypotheses) {
    ch.push(num(cl(h)));
    ch.push(new Paragraph({ spacing: { after: 110, line: 276 }, indent: { left: 540 }, children: [run("Confirming question: ", { bold: true, italics: true }), run(cl(q), { italics: true })] }));
  }
  ch.push(h2("Cautions for sensitive findings"));
  for (const t of P.cautions) ch.push(bullet(cl(t)));

  ch.push(h1("8. Questions for a first conversation"));
  for (const t of P.first_call) ch.push(num(cl(t)));

  ch.push(h1("9. Research coverage tracker", true));
  ch.push(h2("Decisions waiting on the account owner"));
  for (const t of P.waiting) ch.push(bullet(cl(t)));
  const trk = (rows: readonly (readonly string[])[]) =>
    table([2300, 900, 800, 1500, 4000, 2300], ["Source type", "Status", "Priority", "Needs", "What was done (proof)", "What remains"], rows.map(clRow));
  ch.push(h2("Pass 1, summary homework"));
  ch.push(trk(P.tracker_p1));
  ch.push(p(""));
  ch.push(h2("Pass 2, detailed homework"));
  ch.push(trk(sortTracker(P.tracker_p2)));
  ch.push(p(""));
  ch.push(p(cl(trackerTally(b))));

  ch.push(h1("10. Source links", true));
  ch.push(table([900, 7000, 2800], ["ID", "Source", "Domain"], Object.entries(b.sources).map(([id, s]) => [
    id,
    new Paragraph({ spacing: { after: 40, line: 252 }, children: [new ExternalHyperlink({ link: s.url, children: [new TextRun({ text: cl(s.name), font: FONT, size: 17, color: "1155CC", underline: {} })] })] }),
    hostOf(s.url)])));
  ch.push(p(""));
  ch.push(p("Job postings read (J IDs) are linked in Part C of the Sources and Evidence file and in the Job Postings workbook."));

  return makeDoc(ch, true);
}

// The tally line under the trackers, counted from the rows in the order they were given.
export function trackerTally(b: AccountBundle): string {
  const all = [...b.prose.tracker_p1, ...b.prose.tracker_p2];
  const tally = new Map<string, number>();
  for (const r of all) tally.set(r[1] ?? "", (tally.get(r[1] ?? "") ?? 0) + 1);
  return `Tally, computed from the rows above (${all.length} rows): ${[...tally].map(([k, v]) => `${k} ${v}`).join(", ")}.`;
}
