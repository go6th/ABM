// The tokens in ui/tokens.css meet contrast: text at least 4.5:1 on every surface it sits on,
// each pill's ink on its own fill, and the admin console's text on its navy. The values are
// read from the stylesheet itself, so the test checks what ships.
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";
import { ratio } from "@/ui/contrast";

const css = readFileSync(path.resolve(import.meta.dirname, "../../ui/tokens.css"), "utf8");

function block(selector: string): string {
  const start = css.indexOf(selector);
  if (start < 0) throw new Error(`missing ${selector}`);
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}" && --depth === 0) return css.slice(open + 1, i);
  }
  throw new Error("unbalanced");
}

function tokens(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [, name, value] of body.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b/g)) out[name!] = value!;
  return out;
}

const t = tokens(block(":root {"));

const SURFACES = ["bg", "surface"];
const PILLS = ["ok", "probable", "warn", "run", "internal", "neutral", "bad", "lead"];

const TEXT: [string, string][] = [
  ...SURFACES.flatMap((s) => ["ink", "ink-2", "text-3", "text-nav", "muted"].map((fg) => [fg, s] as [string, string])),
  ...PILLS.map((p) => [`${p}-ink`, `${p}-bg`] as [string, string]),
  ["admin-text", "admin-bg"],
  ["admin-text", "admin-card"],
  ["accent", "surface"],
];

describe("text contrast", () => {
  test.each(TEXT)("text %s on %s is at least 4.5:1", (fg, bg) => {
    expect(t[fg], fg).toBeDefined();
    expect(t[bg], bg).toBeDefined();
    expect(ratio(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
  });

  test("white text on the accent fill (the primary button) is at least 4.5:1", () => {
    expect(ratio("#FFFFFF", t["accent"]!)).toBeGreaterThanOrEqual(4.5);
  });
});

test("the page is light only: no dark theme block is shipped half-finished", () => {
  expect(css).not.toContain("data-theme");
});
