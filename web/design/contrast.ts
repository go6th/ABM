// WCAG 2.x contrast (relative luminance and ratio), the pair check over every declared token pair in
// both themes, and the runtime check of a tenant's accent (VW6, D-92). Pure functions; the CLI
// wrapper is `check-contrast.ts`. Thresholds: 4.5:1 body text, 3:1 large text and UI parts.
import { colourOf, pairs, palettes, type Pair, type PairKind, type Theme } from "./tokens.ts";

export const MINIMUM: Record<PairKind, number> = { body: 4.5, large: 3, ui: 3 };

export function parseHex(value: string): [number, number, number] {
  const m = /^#([0-9a-f]{6})$/i.exec(value.trim());
  if (!m || !m[1]) throw new Error(`not a #rrggbb colour: ${value}`);
  const n = Number.parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance(hex: string): number {
  const [r, g, b] = parseHex(hex);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

export interface PairResult extends Pair { theme: Theme; ratio: number; minimum: number; ok: boolean }

export function checkPairs(list: Pair[] = pairs): PairResult[] {
  const out: PairResult[] = [];
  for (const theme of Object.keys(palettes) as Theme[]) {
    for (const pair of list) {
      const r = ratio(colourOf(theme, pair.fg), colourOf(theme, pair.bg));
      const minimum = MINIMUM[pair.kind];
      out.push({ ...pair, theme, ratio: Math.round(r * 100) / 100, minimum, ok: r >= minimum });
    }
  }
  return out;
}

export interface AccentVerdict { requested: string | null; used: string; ok: boolean; ratio: number | null; reason: "ok" | "absent" | "malformed" | "low_contrast" }

/** A tenant accent is used for the brand role only when white-on-accent text (light theme) passes
 *  4.5:1; otherwise the safe default is used and the reason is returned for the tenant admin. */
export function tenantAccent(requested: string | null | undefined, theme: Theme = "light"): AccentVerdict {
  const fallback = palettes[theme].brand;
  if (!requested) return { requested: null, used: fallback, ok: false, ratio: null, reason: "absent" };
  let r: number;
  try {
    r = ratio(requested, palettes.light.brandText);
  } catch {
    return { requested, used: fallback, ok: false, ratio: null, reason: "malformed" };
  }
  const rounded = Math.round(r * 100) / 100;
  if (r < MINIMUM.body) return { requested, used: fallback, ok: false, ratio: rounded, reason: "low_contrast" };
  return { requested, used: theme === "light" ? requested.toLowerCase() : fallback, ok: true, ratio: rounded, reason: "ok" };
}
