// The AccountBundle as the renderer sees it: the zod schema generated from the JSON Schema
// (src/bundle.gen.ts, `npm run gen`), its TypeScript type, and the small readings of `meta`
// every file shares (the file prefix, the dates, the run timestamp the files are stamped with).
import { z } from "zod";
import { AccountBundleSchema, type AccountBundle } from "./bundle.gen.js";
import { sentence } from "./text.js";

export { AccountBundleSchema };
export type { AccountBundle };

export interface Invalid {
  path: string;
  message: string;
}

// Parses untrusted JSON into a bundle, or lists what is wrong with it.
export function parseBundle(raw: unknown): { ok: true; bundle: AccountBundle } | { ok: false; issues: Invalid[] } {
  const r = AccountBundleSchema.safeParse(raw);
  if (r.success) return { ok: true, bundle: r.data };
  const issues = r.error.issues.map((i: z.core.$ZodIssue) => ({ path: i.path.map(String).join(".") || "(root)", message: i.message }));
  return { ok: false, issues };
}

// What every file prints from meta.
export interface Meta {
  prefix: string;
  legal: string;
  runDate: string;
  pullDate: string;
  pullDateShort: string;
  pullDayMonth: string;
  client: string;
  byline: string;
  stampedAt: Date;
}

export function meta(b: AccountBundle): Meta {
  const m = b.meta;
  const pullDateShort = m.pull_date_short ?? m.pull_date;
  return {
    prefix: m.account_short,
    legal: m.account_legal,
    runDate: m.run_date,
    pullDate: m.pull_date,
    pullDateShort,
    // the column heading "Open on 19 Oct": the short pull date without its year
    pullDayMonth: pullDateShort.replace(/\s+\d{4}$/, ""),
    client: m.pursuing_client_display,
    byline: sentence(m.byline),
    stampedAt: new Date(`${m.window.to}T00:00:00Z`),
  };
}

// A source ID in brackets after a heading, or nothing when the bundle has none for it.
export function cite(id: string | undefined): string {
  return id ? ` (${id})` : "";
}

export const fileNames = (prefix: string) => ({
  summary: `${prefix}_Account_Summary.docx`,
  evidence: `${prefix}_Sources_and_Evidence.docx`,
  highlights: `${prefix}_Lane_Highlights.docx`,
  internal: `${prefix}_Internal_Only.docx`,
  people: `${prefix}_People_List.xlsx`,
  jobs: `${prefix}_Job_Postings.xlsx`,
  stack: `${prefix}_Tech_Stack_and_Teams.xlsx`,
});
