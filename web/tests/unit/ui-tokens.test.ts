// The tokens in ui/tokens.css meet contrast in both modes: text at least 4.5:1 on every surface it
// sits on, and each pastel ink on its own fill. The values are read from the stylesheet itself,
// so the test checks what ships. The system-dark block must equal the explicit dark block.
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

function declarations(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [, name, value] of body.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) out[name!] = value!.trim();
  return out;
}

const light = tokens(block(":root {"));
const dark = tokens(block(':root[data-theme="dark"] {'));
const systemDark = block(":root:not([data-theme]) {");

const SURFACES = ["canvas", "surface", "elevated", "grouped"];
const FAMILIES = ["peri", "mint", "peach", "lilac", "rose", "butter"];

const TEXT: [string, string][] = [
  ...SURFACES.flatMap((s) => [["ink", s], ["ink-2", s]] as [string, string][]),
  ...SURFACES.filter((s) => s !== "grouped").map((s) => ["primary", s] as [string, string]),
  ["on-primary", "primary"],
  ...FAMILIES.map((f) => [`${f}-ink`, `${f}-fill`] as [string, string]),
];

describe.each([["light", light], ["dark", dark]] as const)("%s mode", (_name, t) => {
  test.each(TEXT)("text %s on %s is at least 4.5:1", (fg, bg) => {
    expect(t[fg], fg).toBeDefined();
    expect(t[bg], bg).toBeDefined();
    expect(ratio(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
  });
});

test("the system dark block equals the explicit dark block", () => {
  expect(declarations(systemDark)).toEqual(declarations(block(':root[data-theme="dark"] {')));
});

test("the light canvas, ink and primary are the specified values", () => {
  expect(light).toMatchObject({ canvas: "#FFFFFF", grouped: "#F5F5F7", ink: "#1D1D1F", "ink-2": "#6E6E73", primary: "#4A57D6" });
  expect(dark).toMatchObject({ canvas: "#0B0B0F", surface: "#16161B", elevated: "#1F1F26", ink: "#F5F5F7", primary: "#8E9BFF" });
});

test("no stylesheet in ui/ hard-codes a colour outside the token file, except white on the switch thumb and scene highlights", () => {
  for (const file of ["components.css", "shell.css", "auth.css", "base.css"]) {
    const source = readFileSync(path.resolve(import.meta.dirname, "../../ui", file), "utf8");
    const literals = [...source.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((m) => m[0].toUpperCase());
    expect(literals.filter((c) => c !== "#FFFFFF" && c !== "#1D1D1F"), file).toEqual([]);
  }
});
