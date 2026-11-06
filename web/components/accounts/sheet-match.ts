// The match a deep-dive table is narrowed by (an Ask citation's link: ?match_col=location&match=…)
// as query parameters. A plain module (no "use client"), so the server page and the client table
// build their links with the same rule: every link carries the match, so a sort, a chip, a page,
// a tab or a download stays within the matched rows.
import type { Sheet } from "@/lib/gen/api.gen";

export function matchParams(sheet: Pick<Sheet, "match">): Record<string, string> {
  const m = sheet.match;
  return m && m.col && m.value ? { match_col: m.col, match: m.value } : {};
}
