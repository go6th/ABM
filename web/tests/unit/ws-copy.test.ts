// Copy lint for the client workspace: every string in lib/messages.ts and every JSX text node in
// components/ws must avoid dash characters, spaced hyphen dashes, arrows, middle-dot meta strings,
// ALL CAPS words, and the backend's client-safe denylist (lib/gen/labels.gen.ts, generated from
// the backend config, so nothing is retyped here).
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "vitest";
import { m } from "@/lib/messages";
import { DENYLIST_PATTERNS } from "@/lib/gen/labels.gen";

/** Every literal string in the messages module (functions are sampled once). */
function allStrings(value: unknown = m): string[] {
  if (typeof value === "string") return [value];
  if (typeof value === "function") {
    const sample = (value as (...a: unknown[]) => unknown)(2, 9, "sample");
    return typeof sample === "string" ? [sample] : [];
  }
  if (Array.isArray(value)) return value.flatMap((v) => allStrings(v));
  if (value && typeof value === "object") return Object.values(value).flatMap((v) => allStrings(v));
  return [];
}

const RULES: [string, RegExp][] = [
  ["em or en dash", /[–—]/],
  ["spaced hyphen used as a dash", /\s-\s/],
  ["arrow", /[←-⇿➔➡]|->|=>/],
  ["middle dot", /·|•/],
  // Acronyms that are written in capitals are not shouting.
  ["ALL CAPS word", /\b(?!(?:JPEG|JSON|SAML|UUID|HTTPS)\b)[A-Z]{4,}\b/],
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
      // a ternary between two elements is code, not copy
      if (!t || /[?=]/.test(t)) continue;
      if (copyProblems(t).length) bad.push(`${file}: ${t}`);
    }
  }
  expect(bad).toEqual([]);
});

/** Every .tsx file under a directory, recursively. */
function tsxUnder(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? tsxUnder(full) : entry.name.endsWith(".tsx") ? [full] : [];
  });
}

test("JSX text and quoted labels on every client and sign-in screen pass the copy rules", () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const files = [
    ...tsxUnder(path.join(root, "app/(client)")),
    ...tsxUnder(path.join(root, "app/(auth)")),
    ...tsxUnder(path.join(root, "ui")),
    path.join(root, "components/ProviderKey.tsx"),
  ];
  const bad: string[] = [];
  for (const file of files) {
    const source = readFileSync(file, "utf8").replace(/\/\/[^\n]*|\/\*[\s\S]*?\*\//g, "");
    const texts = [
      ...[...source.matchAll(/>([^<>{}]*[A-Za-z][^<>{}]*)</g)].map((m) => m[1]!),
      ...[...source.matchAll(/(?:label|title|body|sub|action|placeholder|aria-label)=\{?"([^"]+)"/g)].map((m) => m[1]!),
    ];
    for (const text of texts) {
      const t = text.trim();
      // "=>" inside JSX is code, not copy; class lists and paths are not copy either
      if (!t || /=>|[;=]|^[a-z0-9 _/.:#-]+$/.test(t)) continue;
      if (copyProblems(t).length) bad.push(`${path.relative(root, file)}: ${t}`);
    }
  }
  expect(bad).toEqual([]);
});
