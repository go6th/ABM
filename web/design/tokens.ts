// Design tokens: the single source of every colour, size, shadow and motion value (D-91, D-92).
// `tokens.css` is generated from this file (`node design/build-tokens.ts`); a test fails when the
// two differ and when any colour literal appears outside them. Colours are named by ROLE, never by
// hue, and pastels are only ever surfaces and accents: text uses the text roles or an accent's
// `strong` step, and every text-on-surface pair below is contrast-checked in both themes
// (`design/check-contrast.ts`).

export type Theme = "light" | "dark";
export type Hex = `#${string}`;

export const ACCENTS = ["peach", "lavender", "mint", "sky", "rose", "butter", "slate"] as const;
export type Accent = (typeof ACCENTS)[number];
export interface AccentSteps { soft: Hex; mid: Hex; strong: Hex }

export interface Palette {
  backdrop: Hex; canvas: Hex; raised: Hex; sunken: Hex; glass: string; glassStrong: string;
  border: Hex; borderStrong: Hex; focus: Hex;
  textPrimary: Hex; textSecondary: Hex; textMuted: Hex; textInverse: Hex;
  brand: Hex; brandText: Hex; brandSoft: Hex;
  operator: Hex; operatorText: Hex; operatorSoft: Hex;
  shadowTint: string; highlight: string;
  accents: Record<Accent, AccentSteps>;
}

export const palettes: Record<Theme, Palette> = {
  light: {
    backdrop: "#efe9f3", canvas: "#faf7f4", raised: "#ffffff", sunken: "#f2ede7",
    glass: "rgb(255 255 255 / 0.66)", glassStrong: "rgb(255 255 255 / 0.84)",
    border: "#e5ddd3", borderStrong: "#857c90", focus: "#5b45b0",
    textPrimary: "#1f1b24", textSecondary: "#4a4452", textMuted: "#615a6b", textInverse: "#ffffff",
    brand: "#5b45b0", brandText: "#ffffff", brandSoft: "#ece6fb",
    operator: "#2a2230", operatorText: "#ffd9bf", operatorSoft: "#fde3d3",
    shadowTint: "62 44 92", highlight: "rgb(255 255 255 / 0.9)",
    accents: {
      peach: { soft: "#fde6d8", mid: "#f6b996", strong: "#9a4320" },
      lavender: { soft: "#ece6fb", mid: "#c5b6f1", strong: "#4f3aa3" },
      mint: { soft: "#dcf3e7", mid: "#9fd9bc", strong: "#1c6b45" },
      sky: { soft: "#ddedfa", mid: "#a1cbef", strong: "#1d5788" },
      rose: { soft: "#fbe2e7", mid: "#f0abb9", strong: "#9b2c45" },
      butter: { soft: "#fbf0cc", mid: "#eed584", strong: "#735409" },
      slate: { soft: "#ebe8e4", mid: "#c8c2ba", strong: "#4a4540" },
    },
  },
  dark: {
    backdrop: "#100e15", canvas: "#16141c", raised: "#1f1c27", sunken: "#121017",
    glass: "rgb(31 28 39 / 0.66)", glassStrong: "rgb(31 28 39 / 0.86)",
    border: "#2f2a3a", borderStrong: "#7d7490", focus: "#c9bbff",
    textPrimary: "#f4f0fa", textSecondary: "#d0c9dc", textMuted: "#aaa2b8", textInverse: "#16141c",
    brand: "#c9bbff", brandText: "#1b1530", brandSoft: "#2b2443",
    operator: "#ffcfae", operatorText: "#2a1a10", operatorSoft: "#3a2a20",
    shadowTint: "0 0 0", highlight: "rgb(255 255 255 / 0.06)",
    accents: {
      peach: { soft: "#3a261c", mid: "#7a4a33", strong: "#ffc6a6" },
      lavender: { soft: "#2a2342", mid: "#4f4180", strong: "#cdbfff" },
      mint: { soft: "#17322a", mid: "#2d6650", strong: "#a6e6c6" },
      sky: { soft: "#172b3e", mid: "#2c5579", strong: "#a9d3f5" },
      rose: { soft: "#3a1d27", mid: "#77384b", strong: "#f6b3c2" },
      butter: { soft: "#342b12", mid: "#6e5a20", strong: "#f2dc94" },
      slate: { soft: "#26232c", mid: "#4a4553", strong: "#d6d0de" },
    },
  },
};

/** Semantic roles map onto accent families (colour is never the only signal: see `signals`). */
export const semantic = { success: "mint", warning: "butter", danger: "rose", info: "sky", neutral: "slate" } as const satisfies Record<string, Accent>;

/** Evidence grades (product and engine vocabularies, D-01) with family, icon and text. */
export const grades = {
  confirmed: { label: "Confirmed", accent: "mint", icon: "badge-check" },
  probable: { label: "Probable", accent: "sky", icon: "circle-dot" },
  hypothesis: { label: "Hypothesis", accent: "lavender", icon: "lightbulb" },
  lead: { label: "Lead", accent: "butter", icon: "search" },
  conflicting: { label: "Conflicting", accent: "rose", icon: "git-compare" },
  blocked: { label: "Blocked", accent: "slate", icon: "ban" },
} as const satisfies Record<string, { label: string; accent: Accent; icon: string }>;

/** Coverage tracker statuses (rule 10) with family, icon and text. */
export const trackerStatuses = {
  done: { label: "Done", accent: "mint", icon: "check" },
  partial: { label: "Partial", accent: "butter", icon: "circle-half" },
  not_done: { label: "Not done", accent: "peach", icon: "circle-dashed" },
  blocked: { label: "Blocked", accent: "rose", icon: "ban" },
  none: { label: "None", accent: "slate", icon: "minus" },
} as const satisfies Record<string, { label: string; accent: Accent; icon: string }>;

export const radii = { xs: "6px", sm: "10px", md: "14px", lg: "20px", xl: "28px", pill: "999px" } as const;
export const space = { 0: "0", 1: "4px", 2: "8px", 3: "12px", 4: "16px", 5: "20px", 6: "24px", 8: "32px", 10: "40px", 12: "48px", 16: "64px" } as const;
export const type = {
  xs: ["12px", "16px"], sm: ["13px", "19px"], md: ["15px", "23px"], lg: ["17px", "26px"],
  xl: ["21px", "29px"], "2xl": ["27px", "34px"], "3xl": ["35px", "42px"], "4xl": ["46px", "52px"],
} as const;
export const fonts = {
  sans: "var(--font-sans), ui-sans-serif, system-ui, sans-serif",
  serif: "var(--font-serif), ui-serif, Georgia, serif",
  mono: "var(--font-mono), ui-monospace, monospace",
} as const;
/** Elevation 0 to 5: a tight contact shadow plus a soft ambient one, tinted (never pure black). */
export const elevation = [0, 1, 2, 3, 4, 5] as const;
export function shadow(level: number, alpha = 1): string {
  if (level === 0) return "none";
  const contact = `0 ${level}px ${level * 2}px rgb(var(--shadow-tint) / ${(0.06 + level * 0.012) * alpha})`;
  const ambient = `0 ${level * 6}px ${level * 14}px -${level * 2}px rgb(var(--shadow-tint) / ${(0.08 + level * 0.02) * alpha})`;
  return `${contact}, ${ambient}, inset 0 1px 0 var(--highlight)`;
}
export const blur = { glass: "14px", glassStrong: "22px" } as const;
export const motion = {
  fast: "120ms", base: "200ms", slow: "320ms", blob: "38s",
  easeOut: "cubic-bezier(0.2, 0.8, 0.2, 1)", easeInOut: "cubic-bezier(0.6, 0, 0.3, 1)",
  spring: "cubic-bezier(0.3, 1.4, 0.5, 1)",
} as const;
export const z = { base: 0, raised: 10, sticky: 20, sidebar: 30, panel: 40, overlay: 50, dialog: 60, toast: 70, banner: 80 } as const;
export const layout = { sidebar: "272px", sidebarCollapsed: "72px", column: "880px", panel: "440px", gutter: "16px" } as const;

/** Text and icon pairs that must pass WCAG contrast in both themes. `mid` steps are decorative
 *  only (a chip's edge beside its text label, a blob); any meaningful border, meter or icon uses
 *  `strong` or `borderStrong`, which are checked here. */
export type PairKind = "body" | "large" | "ui";
export interface Pair { fg: string; bg: string; kind: PairKind; note: string }

function accentPairs(): Pair[] {
  const out: Pair[] = [];
  for (const a of ACCENTS) {
    out.push({ fg: `accents.${a}.strong`, bg: `accents.${a}.soft`, kind: "body", note: `${a} chip text` });
    out.push({ fg: `accents.${a}.strong`, bg: "raised", kind: "body", note: `${a} text on a card` });
    out.push({ fg: `accents.${a}.strong`, bg: "canvas", kind: "body", note: `${a} text on the canvas` });
    out.push({ fg: `accents.${a}.strong`, bg: "sunken", kind: "ui", note: `${a} meter fill on a track` });
  }
  return out;
}

export const pairs: Pair[] = [
  ...(["canvas", "raised", "sunken", "backdrop", "brandSoft"] as const).flatMap((bg) => [
    { fg: "textPrimary", bg, kind: "body" as const, note: `primary text on ${bg}` },
    { fg: "textSecondary", bg, kind: "body" as const, note: `secondary text on ${bg}` },
    { fg: "textMuted", bg, kind: "body" as const, note: `muted text on ${bg}` },
  ]),
  { fg: "brandText", bg: "brand", kind: "body", note: "primary button label" },
  { fg: "brand", bg: "raised", kind: "body", note: "link on a card" },
  { fg: "brand", bg: "canvas", kind: "body", note: "link on the canvas" },
  { fg: "focus", bg: "canvas", kind: "ui", note: "focus ring on the canvas" },
  { fg: "focus", bg: "raised", kind: "ui", note: "focus ring on a card" },
  { fg: "borderStrong", bg: "raised", kind: "ui", note: "input border on a card" },
  { fg: "operatorText", bg: "operator", kind: "body", note: "operator bar text" },
  { fg: "textPrimary", bg: "operatorSoft", kind: "body", note: "text on the operator tint" },
  ...accentPairs(),
];

export function colourOf(theme: Theme, role: string): string {
  const p = palettes[theme] as unknown as Record<string, unknown>;
  const value = role.split(".").reduce<unknown>((node, key) => (node as Record<string, unknown> | undefined)?.[key], p);
  if (typeof value !== "string") throw new Error(`unknown colour role ${role}`);
  return value;
}
