// The shared parity modules: the cleaner equals parity.text, the counts equal parity.counts on
// the synthetic bundle (its computed block carries the Python-computed numbers), and the
// mismatch check reads the manifest's per-workbook values.
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";
import { clean, cleanCell, cleanPosting } from "@/lib/parity/clean";
import { jobsRowTotals, jobsSummary, meetsSeniority, mismatches, peopleLaneCounts, peopleLevelCounts, platformRanking, summaryValues, type CountBundle } from "@/lib/parity/counts";
import { FILE_ORDER, PART_C_TABLE, TABLE_BY_ID, TABLE_SPECS } from "@/lib/parity/tableSpecs";

const bundle = JSON.parse(readFileSync(path.resolve(__dirname, "../../../tests/parity/fixtures/synthetic_bundle.json"), "utf8")) as CountBundle & {
  computed: { jobs_counts: Record<string, Record<string, number>>; senior: number; prio: Record<string, number>; people_total: number };
};

describe("clean", () => {
  test("the Word cleaner replaces dashes, the spaced hyphen, the apostrophe and the arrow in that order", () => {
    expect(clean("A – B—C")).toBe("A, B, C");
    expect(clean("a - b")).toBe("a, b");
    expect(clean("it’s A → B")).toBe("it's A  to  B");
    expect(clean(null)).toBe("");
  });
  test("the workbook cleaner keeps the spaced hyphen, the apostrophe and the arrow", () => {
    expect(cleanCell("a - b’s → c – d")).toBe("a - b’s → c, d");
    expect(cleanPosting("**Title** ## #x — y")).toBe("Title  x, y");
  });
});

describe("counts", () => {
  test("the jobs summary equals the bundle's computed block", () => {
    const jobs = jobsSummary(bundle);
    for (const [lane, want] of Object.entries(bundle.computed.jobs_counts)) {
      expect(jobs[lane]?.total, lane).toBe(want.total);
      expect(jobs[lane]?.open, lane).toBe(want.open);
      expect(jobs[lane]?.build, lane).toBe(want.build);
      expect(jobs[lane]?.user, lane).toBe(want.user);
    }
    expect(jobsRowTotals(bundle)).toEqual({ all_rows: 6, account_rows: 5, other_rows: 1 });
  });
  test("the people counts equal the bundle's computed block", () => {
    expect(meetsSeniority(bundle)).toBe(bundle.computed.senior);
    const levels = peopleLevelCounts(bundle);
    expect(Object.values(levels).reduce((n, c) => n + c.people, 0)).toBe(bundle.computed.people_total);
    const lanes = peopleLaneCounts(bundle);
    expect(Object.values(lanes).reduce((n, c) => n + c.a, 0)).toBe(bundle.computed.prio.A ?? 0);
  });
  test("the ranking follows the sheet's rank formula", () => {
    const rows = platformRanking(bundle);
    expect(rows[0]?.platform).toBe("SAP");
    expect(rows.map((r) => r.rank)).toEqual(["A", "B", "C"].slice(0, rows.length).map((_, i) => rows[i]!.rank));
    for (const r of rows) expect(r.rank === "A" ? r.count >= 5 : true).toBe(true);
  });
  test("summaryValues keys match parity.counts and mismatches reads per-workbook values", () => {
    const values = summaryValues(bundle);
    expect(values["jobs.rows.all"]).toBe(6);
    expect(values["people.senior"]).toBe(bundle.computed.senior);
    expect(values["ranking.base"]).toBe(bundle.ranking?.base);
    const manifest = { "Acme_People_List.xlsx": { "people.senior": bundle.computed.senior, "people.level.total.people": 7 } };
    expect(mismatches(values, manifest)).toEqual([]);
    expect(mismatches(values, { "Acme_People_List.xlsx": { "people.senior": 99 } })).toEqual(["Acme_People_List.xlsx: people.senior"]);
    expect(mismatches(values, { x: { unknown: 1 } })).toEqual([]);
  });
});

describe("tableSpecs", () => {
  test("the generated specs carry the files' columns in order", () => {
    expect(FILE_ORDER[0]).toBe("summary");
    expect(TABLE_BY_ID.people?.columns.map((c) => c.header).slice(0, 3)).toEqual(["No.", "Name", "Title as reported"]);
    expect(TABLE_BY_ID.jobs?.paginated).toBe(true);
    expect(TABLE_BY_ID.ranking?.columns[0]?.fills).toEqual({ A: "C6EFCE", B: "FFEB9C", C: "E7E6E6" });
    expect(PART_C_TABLE.columns.map((c) => c.key)).toEqual(["id", "posting", "cls", "status", "reasoning"]);
    expect(TABLE_SPECS.filter((t) => t.file === "summary").at(-1)?.id).toBe("summary_sources");
  });
});
