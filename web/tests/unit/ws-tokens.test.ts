// The workspace tokens in design/ws/ws.css meet contrast in both themes: text at least 4.5:1,
// marks and button fills at least 3:1. The values are read from the stylesheet itself, so the
// test checks what ships. The dark set under the media query and under [data-theme="dark"]
// must be identical.
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";
import { ratio } from "@/design/contrast";

const css = readFileSync(path.resolve(import.meta.dirname, "../../design/ws/ws.css"), "utf8");

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

export function tokens(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [, name, value] of body.matchAll(/--([a-z-]+):\s*(#[0-9a-fA-F]{6})\b/g)) out[name!] = value!;
  return out;
}

const light = tokens(block(".ws {"));
const dark = tokens(block('.ws[data-theme="dark"] {'));
const darkMedia = tokens(block('.ws:not([data-theme="light"]) {'));

const TEXT: [string, string][] = [
  ["ink", "surface"], ["ink", "paper"], ["muted", "surface"], ["muted", "paper"],
  ["spruce", "surface"], ["spruce", "paper"], ["spruce", "spruce-tint"], ["on-spruce", "spruce"],
  ["ochre-ink", "ochre-tint"], ["ochre-ink", "surface"], ["on-ochre", "ochre"],
  ["brick", "surface"], ["brick", "brick-tint"], ["muted", "hover"], ["ink", "hover"],
];
const MARKS: [string, string][] = [["ochre", "surface"], ["spruce", "surface"], ["brick", "paper"]];

describe.each([["light", light], ["dark", dark]] as const)("%s theme", (_name, t) => {
  test.each(TEXT)("text %s on %s is at least 4.5:1", (fg, bg) => {
    expect(t[fg], fg).toBeDefined();
    expect(t[bg], bg).toBeDefined();
    expect(ratio(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
  });
  test.each(MARKS)("mark %s on %s is at least 3:1", (fg, bg) => {
    expect(ratio(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(3);
  });
});

test("the dark set is defined once in meaning: media query and attribute agree", () => {
  expect(darkMedia).toEqual(dark);
});

test("the light tokens are the section 3.2 values", () => {
  expect(light).toMatchObject({
    paper: "#F3F5F2", surface: "#FFFFFF", ink: "#1A2430", muted: "#5C6773", rule: "#D9DED9",
    spruce: "#0E6B5B", "spruce-tint": "#E1F0EC", ochre: "#A8650B", "ochre-ink": "#7A4A06",
    "ochre-tint": "#FBF0DC", brick: "#B3321F",
  });
});
