// Renders `tokens.css` from `tokens.ts` (the one source). The application uses the light palette
// consistently so every route matches the customer sign-in experience.
import { ACCENTS, blur, elevation, fonts, layout, motion, palettes, radii, shadow, space, type, z, type Palette } from "./tokens.ts";

function kebab(s: string): string {
  return s.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
}

function colours(p: Palette): string[] {
  const out: string[] = [];
  for (const [key, value] of Object.entries(p)) {
    if (key === "accents") continue;
    out.push(`--${kebab(key)}: ${value as string};`);
  }
  for (const a of ACCENTS) {
    const steps = p.accents[a];
    out.push(`--${a}-soft: ${steps.soft};`, `--${a}-mid: ${steps.mid};`, `--${a}-strong: ${steps.strong};`);
  }
  return out;
}

function constants(): string[] {
  const out: string[] = [];
  for (const [k, v] of Object.entries(radii)) out.push(`--radius-${k}: ${v};`);
  for (const [k, v] of Object.entries(space)) out.push(`--space-${k}: ${v};`);
  for (const [k, [size, line]] of Object.entries(type)) out.push(`--text-${k}: ${size};`, `--leading-${k}: ${line};`);
  for (const [k, v] of Object.entries(fonts)) out.push(`--family-${k}: ${v};`);
  for (const level of elevation) out.push(`--elevation-${level}: ${shadow(level)};`);
  for (const [k, v] of Object.entries(blur)) out.push(`--blur-${kebab(k)}: ${v};`);
  for (const [k, v] of Object.entries(motion)) out.push(`--motion-${kebab(k)}: ${v};`);
  for (const [k, v] of Object.entries(z)) out.push(`--z-${k}: ${v};`);
  for (const [k, v] of Object.entries(layout)) out.push(`--layout-${kebab(k)}: ${v};`);
  return out;
}

const indent = (lines: string[], n = 2) => lines.map((l) => `${" ".repeat(n)}${l}`).join("\n");

export function tokensCss(): string {
  return [
    "/* GENERATED from design/tokens.ts by `npm run tokens`. Do not edit. */",
    `:root {\n${indent(constants())}\n${indent(colours(palettes.light))}\n  color-scheme: light;\n}`,
    "",
  ].join("\n");
}
