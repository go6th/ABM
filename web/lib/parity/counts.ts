// The workbook Summary counts, computed from the bundle rows: a mirror of parity.counts (Python),
// function for function, with the same Excel semantics (COUNTIF text criteria compare without
// case, "*X*" is a substring match, "P*" a prefix match). The account workspace computes them
// from the rows the API sends and asserts them equal to the manifest's recalculated values.

export interface CountJobRow {
  platforms?: readonly string[] | null;
  entity?: string | null;
  status?: string | null;
  cls?: string | null;
  title?: string | null;
}

export interface CountPersonRow {
  level?: number | string | null;
  senior?: boolean | null;
  lane?: string | null;
  priority?: string | null;
}

export interface CountRankingRow {
  platform: string;
  count?: number | null;
  open?: number | null;
}

export interface CountBundle {
  meta: { account_short: string; lanes?: readonly string[] | null; people_lanes?: readonly string[] | null };
  jobs_rows?: readonly CountJobRow[] | null;
  people_rows?: readonly CountPersonRow[] | null;
  ranking?: { base?: number | null; rows?: readonly CountRankingRow[] | null } | null;
}

export interface JobsCount { total: number; closed: number; open: number; build: number; admin: number; tool: number; user: number }
export interface RowTotals { all_rows: number; account_rows: number; other_rows: number }
export interface LevelCount { people: number; priority_a: number }
export interface LaneCount { a: number; b: number; c: number; total: number }
export interface RankRow { platform: string; count: number; open: number; share: number; rank: string }

export const LEVEL_LABELS: readonly string[] = [
  "C-level, EVP and SVP", "VP", "Senior Director", "Director", "Associate Director", "Head", "Senior Manager", "Manager", "Lead, owner, specialist or developer",
];
export const CLASS_LABELS: Record<string, string> = { build: "Build role", admin: "Admin and owner role", tool: "Working tool", user: "User role" };
export const PRIORITIES = ["A", "B", "C"] as const;
const RANK_A: [number, number] = [0.15, 5];
const RANK_B: [number, number] = [0.05, 3];
const RANK_C_MIN = 2;

// Python's str.casefold() on the characters these cells carry is lower-casing.
const fold = (v: unknown): string => (v == null ? "" : String(v)).toLowerCase();

/** The Platforms named cell: the row's platforms joined by comma and space. */
export function platformsNamed(row: CountJobRow): string {
  return (row.platforms ?? []).map(String).join(", ");
}

/** COUNTIFS(..., "*platform*"): the cell contains the word, any case. */
export function namesPlatform(row: CountJobRow, platform: string): boolean {
  return fold(platformsNamed(row)).includes(fold(platform));
}

/** COUNTIFS(Entity, "prefix*"): the Entity cell starts with the prefix, any case. */
export function isAccountRow(row: CountJobRow, prefix: string): boolean {
  return fold(row.entity).startsWith(fold(prefix));
}

const lanes = (b: CountBundle): string[] => (b.meta.lanes ?? []).map(String);

/** The Jobs Summary table: one row per lane platform, account positions only. */
export function jobsSummary(b: CountBundle): Record<string, JobsCount> {
  const prefix = b.meta.account_short;
  const rows = (b.jobs_rows ?? []).filter((r) => isAccountRow(r, prefix));
  const out: Record<string, JobsCount> = {};
  for (const lane of lanes(b)) {
    const laneRows = rows.filter((r) => namesPlatform(r, lane));
    const count = (field: "status" | "cls", value: string) => laneRows.filter((r) => fold(r[field]) === fold(value)).length;
    out[lane] = {
      total: laneRows.length,
      closed: count("status", "Closed"),
      open: count("status", "Open"),
      build: count("cls", CLASS_LABELS.build ?? ""),
      admin: count("cls", CLASS_LABELS.admin ?? ""),
      tool: count("cls", CLASS_LABELS.tool ?? ""),
      user: count("cls", CLASS_LABELS.user ?? ""),
    };
  }
  return out;
}

/** COUNTA(Title), COUNTIF(Entity, "prefix*") and the difference. */
export function jobsRowTotals(b: CountBundle): RowTotals {
  const prefix = b.meta.account_short;
  const rows = b.jobs_rows ?? [];
  const all = rows.filter((r) => String(r.title ?? "") !== "").length;
  const account = rows.filter((r) => isAccountRow(r, prefix)).length;
  return { all_rows: all, account_rows: account, other_rows: all - account };
}

/** The Level cell for a numeric level (an unknown level prints as its number). */
export function levelLabel(level: unknown): string {
  const n = Number.parseInt(String(level), 10);
  if (Number.isNaN(n)) return String(level);
  return LEVEL_LABELS[n - 1] ?? String(level);
}

/** People per level and priority A per level, in the sheet's level order. */
export function peopleLevelCounts(b: CountBundle): Record<string, LevelCount> {
  const rows = b.people_rows ?? [];
  const out: Record<string, LevelCount> = {};
  for (const label of LEVEL_LABELS) {
    const at = rows.filter((r) => fold(levelLabel(r.level)) === fold(label));
    out[label] = { people: at.length, priority_a: at.filter((r) => fold(r.priority) === "a").length };
  }
  return out;
}

/** The Total row under the level table. */
export function peopleLevelTotals(b: CountBundle): LevelCount {
  const counts = Object.values(peopleLevelCounts(b));
  return { people: counts.reduce((n, c) => n + c.people, 0), priority_a: counts.reduce((n, c) => n + c.priority_a, 0) };
}

/** COUNTIF(Meets seniority rule, "Yes"). */
export function meetsSeniority(b: CountBundle): number {
  return (b.people_rows ?? []).filter((r) => !!r.senior).length;
}

/** The lane labels of the People Summary: meta.people_lanes, else the rows' lanes in order. */
export function peopleLanes(b: CountBundle): string[] {
  const given = (b.meta.people_lanes ?? []).map(String);
  if (given.length) return given;
  const seen: string[] = [];
  for (const r of b.people_rows ?? []) {
    const lane = String(r.lane ?? "");
    if (lane && !seen.includes(lane)) seen.push(lane);
  }
  return seen;
}

/** COUNTIFS(Lane, lane, Priority, p, Meets seniority rule, "Yes") per lane and priority. */
export function peopleLaneCounts(b: CountBundle): Record<string, LaneCount> {
  const senior = (b.people_rows ?? []).filter((r) => !!r.senior);
  const out: Record<string, LaneCount> = {};
  for (const lane of peopleLanes(b)) {
    const inLane = senior.filter((r) => fold(r.lane) === fold(lane));
    const [a, bb, c] = PRIORITIES.map((p) => inLane.filter((r) => fold(r.priority) === p.toLowerCase()).length) as [number, number, number];
    out[lane] = { a, b: bb, c, total: a + bb + c };
  }
  return out;
}

/** The Total row under the lane table. */
export function peopleLaneTotals(b: CountBundle): LaneCount {
  const counts = Object.values(peopleLaneCounts(b));
  const sum = (k: keyof LaneCount) => counts.reduce((n, c) => n + c[k], 0);
  return { a: sum("a"), b: sum("b"), c: sum("c"), total: sum("total") };
}

/** IF(base=0, 0, count/base). */
export function shareOf(count: number, base: number): number {
  return base === 0 ? 0 : count / base;
}

/** The rank formula: A, B, C or blank. */
export function rankOf(count: number, share: number): string {
  if (share >= RANK_A[0] && count >= RANK_A[1]) return "A";
  if (share >= RANK_B[0] && count >= RANK_B[1]) return "B";
  return count >= RANK_C_MIN ? "C" : "";
}

/** Every ranked row with its share and rank, in sheet order. */
export function platformRanking(b: CountBundle): RankRow[] {
  const base = Number(b.ranking?.base ?? 0);
  return (b.ranking?.rows ?? []).map((row) => {
    const count = Number(row.count ?? 0);
    const share = shareOf(count, base);
    return { platform: String(row.platform), count, open: Number(row.open ?? 0), share, rank: rankOf(count, share) };
  });
}

/** The Counts block: COUNTIF(Rank, "A") and the same for B and C. */
export function rankCounts(b: CountBundle): Record<string, number> {
  const rows = platformRanking(b);
  return Object.fromEntries(PRIORITIES.map((letter) => [letter, rows.filter((r) => r.rank === letter).length]));
}

/** Every Summary value as one flat mapping, keyed exactly as parity.counts.summary_values. */
export function summaryValues(b: CountBundle): Record<string, number | string> {
  const out: Record<string, number | string> = {};
  for (const [lane, jc] of Object.entries(jobsSummary(b))) {
    for (const [key, value] of Object.entries(jc)) out[`jobs.${lane}.${key}`] = value;
  }
  const totals = jobsRowTotals(b);
  out["jobs.rows.all"] = totals.all_rows;
  out["jobs.rows.account"] = totals.account_rows;
  out["jobs.rows.other"] = totals.other_rows;
  for (const [label, lc] of Object.entries(peopleLevelCounts(b))) {
    out[`people.level.${label}.people`] = lc.people;
    out[`people.level.${label}.priority_a`] = lc.priority_a;
  }
  const lt = peopleLevelTotals(b);
  out["people.level.total.people"] = lt.people;
  out["people.level.total.priority_a"] = lt.priority_a;
  out["people.senior"] = meetsSeniority(b);
  for (const [lane, lc] of Object.entries(peopleLaneCounts(b))) {
    for (const [key, value] of Object.entries(lc)) out[`people.lane.${lane}.${key}`] = value;
  }
  const laneTotals = peopleLaneTotals(b);
  for (const [key, value] of Object.entries(laneTotals)) out[`people.lane.total.${key}`] = value;
  out["ranking.base"] = Number(b.ranking?.base ?? 0);
  for (const row of platformRanking(b)) {
    out[`ranking.${row.platform}.count`] = row.count;
    out[`ranking.${row.platform}.share`] = row.share;
    out[`ranking.${row.platform}.rank`] = row.rank;
  }
  for (const [letter, n] of Object.entries(rankCounts(b))) out[`ranking.count.${letter}`] = n;
  return out;
}

const SHARE_TOLERANCE = 1e-9;

/** The keys of the manifest's recalculated Summary values (per workbook) whose value differs
 * from the computed one; empty when the screen and the files agree. */
export function mismatches(computed: Record<string, number | string>, manifestSummary: Record<string, Record<string, unknown>>): string[] {
  const out: string[] = [];
  for (const [file, values] of Object.entries(manifestSummary)) {
    for (const [key, got] of Object.entries(values)) {
      const want = computed[key];
      if (want === undefined) continue;
      if (typeof want === "number") {
        const n = typeof got === "number" ? got : Number(got);
        if (Number.isNaN(n) || Math.abs(n - want) > SHARE_TOLERANCE) out.push(`${file}: ${key}`);
      } else if (String(got ?? "") !== want) out.push(`${file}: ${key}`);
    }
  }
  return out;
}
