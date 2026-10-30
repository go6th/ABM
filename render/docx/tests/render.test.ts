import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parseBundle, type AccountBundle } from "../src/bundle.js";
import { buildEvidence } from "../src/evidence.js";
import { buildHighlights } from "../src/highlights.js";
import { buildInternalOnly } from "../src/internal_only.js";
import { packFixed } from "../src/pack.js";
import { buildSummary, trackerTally } from "../src/summary.js";
import { cl } from "../src/text.js";
import { readDocx, type ParaBlock } from "./docx_reader.js";
import { makeBundle } from "./synthetic.js";

const stamp = new Date("2026-10-19T00:00:00Z");
const paras = (blocks: readonly { kind: string }[]) => blocks.filter((b): b is ParaBlock => b.kind === "p");

async function summary(b: AccountBundle = makeBundle()) {
  return readDocx(await packFixed(buildSummary(b), stamp));
}

describe("text cleaner", () => {
  it("matches the reference scripts' cl", () => {
    expect(cl("a – b — c - d’s → e")).toBe("a, b, c, d's  to  e");
    expect(cl(null)).toBe("");
    expect(cl(12)).toBe("12");
  });
});

describe("bundle validation", () => {
  it("accepts the synthetic bundle", () => {
    expect(parseBundle(makeBundle()).ok).toBe(true);
  });
  it("rejects unknown keys at any depth, like the JSON Schema", () => {
    const top = { ...makeBundle(), extra: 1 };
    expect(parseBundle(top).ok).toBe(false);
    const nested = makeBundle();
    (nested.meta as Record<string, unknown>).surprise = "x";
    const r = parseBundle(nested);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.issues.some((i) => i.path === "meta")).toBe(true);
  });
  it("rejects a missing required field and a bad enum", () => {
    const b = makeBundle() as unknown as Record<string, Record<string, unknown>>;
    delete b.computed!.people_total;
    expect(parseBundle(b).ok).toBe(false);
    const c = makeBundle();
    (c.jobs_read.J1 as Record<string, unknown>).cls = "Other";
    expect(parseBundle(c).ok).toBe(false);
  });
});

describe("Account Summary", () => {
  it("has the section headings in order, with page breaks where the reference puts them", async () => {
    const d = await summary();
    expect(d.headings).toEqual([
      "How to read this file",
      "1. The account in plain words", "The account picture", "What stands out", "What it likely means for each lane (interpretation labeled)", "How this could be wrong", "The simplest way to sharpen it",
      "2. Recent news (newest first)",
      "3. Company and transformation baseline",
      "4. Signals", "Tier 1, budget proximate", "Tier 2, one step removed", "Tier 3, context and openers", "Context and history (older than the shelf life)",
      "5. Platform picture by lane", "Platform hiring over 12 months (NWI positions only, first seen 19 October 2025 to 19 October 2026)",
      "6. People", "Key people (priority A and the executives the lanes report to)", "Findings from profiles", "People whose status changed", "Interpretation",
      "7. Hypotheses to test", "Cautions for sensitive findings",
      "8. Questions for a first conversation",
      "9. Research coverage tracker", "Decisions waiting on the account owner", "Pass 1, summary homework", "Pass 2, detailed homework",
      "10. Source links",
    ]);
    const h1 = paras(d.blocks).filter((p) => p.style === "Heading1");
    expect(h1.map((p) => p.pageBreakBefore)).toEqual([false, true, true, true, true, true, true, true, false, true, true]);
    expect(h1[0]?.size).toBe(14);
    expect(h1[0]?.color).toBe("1F3A5F");
  });

  it("has fourteen tables of the reference's shapes and widths", async () => {
    const d = await summary();
    expect(d.tables).toHaveLength(14);
    expect(d.tables.map((t) => [t.rows, t.cols])).toEqual([
      [5, 3], [2, 5], [2, 5], [2, 5], [2, 5], [2, 5], [3, 4], [4, 7], [3, 6], [2, 4], [2, 4], [3, 6], [5, 6], [4, 3],
    ]);
    expect(d.tables[0]?.widths).toEqual([2000, 5200, 2400]);
    expect(d.tables[7]?.widths).toEqual([1600, 1000, 1000, 1000, 1500, 1300, 1200]);
    expect(d.tables[7]?.cells[0]).toEqual(["Platform", "Postings naming it", "Open on 19 Oct", "Build roles", "Admin and owner roles", "Working tool", "User role"]);
    expect(d.tables[7]?.cells[1]).toEqual(["Salesforce", "40", "6", "2", "3", "25", "10"]);
    expect(d.tables[13]?.cells[1]).toEqual(["S1", "Northwind leadership page, who we are", "northwind.example"]);
  });

  it("fills the header row navy and a grade cell with its colour", async () => {
    const d = await summary();
    const tier1 = d.tables[2]!;
    expect(tier1.fills[0]).toEqual(Array(5).fill("1F3A5F"));
    expect(tier1.cells[1]?.[1]).toBe("Confirmed");
    expect(tier1.fills[1]?.[1]).toBe("DDEFE0");
    expect(tier1.fills[1]?.[0]).toBeNull();
    expect(d.tables[3]?.fills[1]?.[1]).toBe("FFF1CC");
    expect(d.tables[4]?.fills[1]?.[1]).toBe("FBDAD4");
    expect(d.tables[5]?.fills[1]?.[1]).toBe("E6E6E6");
    expect(d.tables[11]?.fills[2]?.[1]).toBe("FFF1CC");
  });

  it("prints Profile as a link or No link found in the Key people table", async () => {
    const d = await summary();
    const kp = d.tables[8]!;
    expect(kp.cells[1]?.[4]).toBe("Profile");
    expect(kp.cells[2]?.[4]).toBe("No link found");
    expect(Object.values(d.rels)).toContain("https://social.example/in/priya-example");
  });

  it("sorts the pass 2 tracker and computes the tally line from the rows", async () => {
    const d = await summary();
    const p2 = d.tables[12]!;
    expect(p2.cells.slice(1).map((r) => r[0])).toEqual(["Leadership changes", "Earnings calls", "Vendor pages", "Patents"]);
    const tally = paras(d.blocks).find((p) => p.text.startsWith("Tally"));
    expect(tally?.text).toBe("Tally, computed from the rows above (6 rows): Done 4, Partial 1, Not done 1.");
    expect(trackerTally(makeBundle())).toBe(tally?.text);
  });

  it("composes the hiring base paragraph from the entities and counts, or prints the bundle's", async () => {
    const d = await summary();
    const base = paras(d.blocks).find((p) => p.text.startsWith("Base:"));
    expect(base?.text).toBe("Base: 1210 NWI postings in the feed after removing 80 Harbor positions; 130 of them name at least one lane platform (S2). Build role means the platform is the job; Admin and owner role runs or governs it; Working tool means daily use; User role is a tag or boilerplate mention.");
    const b = makeBundle();
    b.prose.hiring_base = "Base: given by the bundle.";
    const e = await summary(b);
    expect(paras(e.blocks).find((p) => p.text.startsWith("Base:"))?.text).toBe("Base: given by the bundle.");
  });

  it("is landscape US Letter with the reference margins and a live page number in the footer", async () => {
    const d = await summary();
    expect(d.page.orient).toBe("landscape");
    expect(d.page.margins).toEqual({ top: 1300, bottom: 1300, left: 1440, right: 1440 });
    expect(d.footerXml).toMatch(/<w:instrText[^>]*>PAGE<\/w:instrText>/);
    expect(d.footerXml).toContain("Page ");
    expect(Object.keys(d.rels).filter((k) => k.startsWith("rIdlnk"))).toHaveLength(4);
  });

  it("uses list numbering for bullets and numbered items", async () => {
    const d = await summary();
    const stands = paras(d.blocks).find((p) => p.text === "A new CIO started in September 2026 (S1).");
    expect(stands?.list).toBe(true);
    const sharpen = paras(d.blocks).find((p) => p.text.startsWith("Ask the CIO"));
    expect(sharpen?.list).toBe(true);
    const picture = paras(d.blocks).find((p) => p.text.startsWith("Northwind sells"));
    expect(picture?.list).toBe(false);
    expect(picture?.text).toBe("Northwind sells lab instruments, mostly to hospitals (S1).");
  });

  it("renders the same bytes twice, stamped with the run date", async () => {
    const a = await packFixed(buildSummary(makeBundle()), stamp);
    const b = await packFixed(buildSummary(makeBundle()), stamp);
    expect(a.equals(b)).toBe(true);
    const d = await readDocx(a);
    expect(d.coreXml).toContain("<dcterms:created xsi:type=\"dcterms:W3CDTF\">2026-10-19T00:00:00.000Z</dcterms:created>");
    expect(d.coreXml).toContain("<dcterms:modified xsi:type=\"dcterms:W3CDTF\">2026-10-19T00:00:00.000Z</dcterms:modified>");
    expect(d.coreXml).not.toContain("Un-named");
  });
});

describe("Sources and Evidence", () => {
  it("has Parts A to I with a lane heading per lane and the right tables", async () => {
    const d = await readDocx(await packFixed(buildEvidence(makeBundle()), stamp));
    expect(d.headings).toEqual([
      "Part A. Source register",
      "Part B. Job posting extracts for postings read in full",
      "Part C. Postings read one by one, by lane",
      "Salesforce: 2 postings read in full (40 NWI postings name it, 6 open on 19 Oct 2026)",
      "SAP: 1 postings read in full (90 NWI postings name it, 12 open on 19 Oct 2026)",
      "ServiceNow: 0 postings read in full (5 NWI postings name it, 1 open on 19 Oct 2026)",
      "Part D. Video, article and release extracts", "Northwind leadership page, who we are (S1)",
      "Part E. Job board captures", "Live postings captured on 19 October 2026 that name a lane platform",
      "Part F. Leads not verified, and items checked and dropped",
      "Part G. How to keep copies of the original pages",
      "Part H. Data pulls", "Technology detections for lane platforms (S3)",
      "Postings naming each lane platform, NWI positions, 12 months plus live captures (S2)",
      "Entity split of the 12 month postings",
      "News feed events (S3), newest first, 1 event; feed only unless the Summary table says Read",
      "Platform teams (S3)",
      "Part I. Full URLs written out",
    ]);
    expect(d.tables.map((t) => [t.rows, t.cols])).toEqual([[4, 6], [3, 6], [3, 5], [2, 5], [2, 5], [2, 4], [3, 6], [4, 7], [4, 2], [2, 4]]);
    expect(d.page.orient).toBe("landscape");
    const partC = d.tables[2]!;
    expect(partC.cells[2]).toEqual(["J2", "SAP Finance Lead (Lisbon, closed, first seen 2026-03-02)", "Admin and owner role", "Closed", "runs the S/4HANA finance template  to  rollout to two plants."]);
    expect(d.tables[6]?.cells[1]).toEqual(["Salesforce", "2025-11-02", "2026-10-03", "0.98", "3", "1"]);
    const empty = paras(d.blocks).find((p) => p.text.startsWith("No build or owner role"));
    expect(empty).toBeDefined();
    const urls = paras(d.blocks).filter((p) => /^[SJ]\d+: https?:/.test(p.text));
    expect(urls.map((p) => p.size)).toEqual([8, 8, 8, 8, 8]);
  });
});

describe("Lane Highlights and the internal-only file", () => {
  it("renders the highlights in portrait with a heading and bullets per section and no tables", async () => {
    const d = await readDocx(await packFixed(buildHighlights(makeBundle())!, stamp));
    expect(d.page.orient).toBe("portrait");
    expect(d.page.width).toBe(12240);
    expect(d.tables).toHaveLength(0);
    expect(d.headings).toEqual(["Salesforce", "SAP"]);
    expect(paras(d.blocks)[0]?.text).toBe("NWI: leading opportunities and signals by lane");
    expect(paras(d.blocks).filter((p) => p.list)).toHaveLength(3);
    const b = makeBundle();
    b.prose.lane_highlights = null;
    expect(buildHighlights(b)).toBeNull();
  });

  it("renders the internal-only file from prose.internal_only", async () => {
    const d = await readDocx(await packFixed(buildInternalOnly(makeBundle())!, stamp));
    expect(paras(d.blocks)[0]?.text).toBe("Internal Only: NWI (Northwind Instruments plc)");
    expect(paras(d.blocks)[1]?.text).toBe("Internal notes from the pursuing client's own records, 19 to 20 October 2026. Not for the client share and not in the workspace zip. Research by 6th agents.");
    expect(d.headings).toEqual(["What was found in the Client's records", "Why it matters", "What the client facing files say instead"]);
    expect(d.page.orient).toBe("portrait");
  });
});

describe("command line", () => {
  const cli = (args: string[]) => spawnSync(process.execPath, ["--import", "tsx", "src/cli.ts", ...args], { encoding: "utf8" });

  it("writes the client files, and the internal-only file only with --internal", () => {
    const dir = mkdtempSync(join(tmpdir(), "render-docx-"));
    const bundle = join(dir, "bundle.json");
    writeFileSync(bundle, JSON.stringify(makeBundle()));
    const out = join(dir, "out");
    const r = cli([bundle, out]);
    expect(r.status).toBe(0);
    expect(r.stdout.trim().split("\n")).toEqual(["NWI_Account_Summary.docx", "NWI_Sources_and_Evidence.docx", "NWI_Lane_Highlights.docx"]);
    expect(existsSync(join(out, "NWI_Internal_Only.docx"))).toBe(false);
    const r2 = cli([bundle, out, "--internal"]);
    expect(r2.status).toBe(0);
    expect(existsSync(join(out, "NWI_Internal_Only.docx"))).toBe(true);
    expect(readFileSync(join(out, "NWI_Account_Summary.docx")).length).toBeGreaterThan(5000);
  });

  it("exits 2 on an invalid bundle", () => {
    const dir = mkdtempSync(join(tmpdir(), "render-docx-"));
    const bundle = join(dir, "bad.json");
    writeFileSync(bundle, JSON.stringify({ ...makeBundle(), stray: true }));
    const r = cli([bundle, join(dir, "out")]);
    expect(r.status).toBe(2);
    expect(r.stderr).toContain("invalid bundle");
    expect(execFileSync(process.execPath, ["-e", "process.exit(0)"]).length).toBe(0);
  });
});
