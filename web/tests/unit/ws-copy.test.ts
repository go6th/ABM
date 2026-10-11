// Copy lint for the client workspace: every string in lib/messages.ts and every JSX text node in
// components/ws must avoid dash characters, spaced hyphen dashes, arrows, middle-dot meta strings,
// ALL CAPS words, and the backend's client-safe denylist (lib/gen/labels.gen.ts, generated from
// the backend config, so nothing is retyped here).
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "vitest";
import { allStrings } from "@/lib/messages";
import { DENYLIST_PATTERNS } from "@/lib/gen/labels.gen";

const RULES: [string, RegExp][] = [
  ["em or en dash", /[–—]/],
  ["spaced hyphen used as a dash", /\s-\s/],
  ["arrow", /[←-⇿➔➡]|->|=>/],
  ["middle dot", /·|•/],
  ["ALL CAPS word", /\b[A-Z]{4,}\b/],
  ...DENYLIST_PATTERNS.map((p) => [`denylist ${p}`, new RegExp(p, "i")] as [string, RegExp]),
];

export function copyProblems(text: string): string[] {
  return RULES.filter(([, re]) => re.test(text)).map(([name]) => name);
}

test("the rules catch what they are meant to catch", () => {
  expect(copyProblems("Refresh — now")).toContain("em or en dash");
  expect(copyProblems("Accounts - list")).toContain("spaced hyphen used as a dash");
  expect(copyProblems("Next →")).toContain("arrow");
  expect(copyProblems("Weekly · 3 changes")).toContain("middle dot");
  expect(copyProblems("SECURE ACCESS")).toContain("ALL CAPS word");
  expect(copyProblems("Found by Firecrawl")).not.toHaveLength(0);
  expect(copyProblems("12 credits used")).not.toHaveLength(0);
  expect(copyProblems("Confirm refresh")).toEqual([]);
});

test("every message passes the copy rules", () => {
  const bad = allStrings().map((s) => [s, copyProblems(s)] as const).filter(([, p]) => p.length);
  expect(bad).toEqual([]);
});

test("JSX text in the workspace components passes the copy rules", () => {
  const dir = path.resolve(import.meta.dirname, "../../components/ws");
  const bad: string[] = [];
  for (const file of readdirSync(dir).filter((f) => f.endsWith(".tsx"))) {
    const source = readFileSync(path.join(dir, file), "utf8");
    for (const [, text] of source.matchAll(/>([^<>{}]*[A-Za-z][^<>{}]*)</g)) {
      const t = text!.trim();
      if (t && copyProblems(t).length) bad.push(`${file}: ${t}`);
    }
  }
  expect(bad).toEqual([]);
});
