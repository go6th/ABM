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
    backdrop: "#f7f8fc", canvas: "#f7f8fc", raised: "#ffffff", sunken: "#f0f2f7",
    glass: "rgb(255 255 255 / 0.66)", glassStrong: "rgb(255 255 255 / 0.84)",
    border: "#e4e8f0", borderStrong: "#8793a5", focus: "#4d69a8",
    textPrimary: "#243047", textSecondary: "#4a5870", textMuted: "#5e6a7e", textInverse: "#ffffff",
    brand: "#4d69a8", brandText: "#ffffff", brandSoft: "#e8f0ff",
    operator: "#4d69a8", operatorText: "#ffffff", operatorSoft: "#e8f0ff",
    shadowTint: "44 60 92", highlight: "rgb(255 255 255 / 0.9)",
    accents: {
      peach: { soft: "#fcebdd", mid: "#efc3a9", strong: "#915134" },
      lavender: { soft: "#f0ebfb", mid: "#c9bde5", strong: "#66528f" },
      mint: { soft: "#e4f3eb", mid: "#a9d7bf", strong: "#326f5a" },
      sky: { soft: "#e8f0ff", mid: "#b8cbed", strong: "#4d69a8" },
      rose: { soft: "#f8e8ed", mid: "#e8bbc8", strong: "#a94c67" },
      butter: { soft: "#fff3d5", mid: "#e9d58f", strong: "#806613" },
      slate: { soft: "#eef1f5", mid: "#cbd2de", strong: "#4b576a" },
    },
  },
  dark: {
    backdrop: "#10151e", canvas: "#151b27", raised: "#1c2431", sunken: "#111722",
    glass: "rgb(28 36 49 / 0.66)", glassStrong: "rgb(28 36 49 / 0.86)",
    border: "#2e394b", borderStrong: "#738198", focus: "#a6c0f5",
    textPrimary: "#eef3fa", textSecondary: "#c5cfde", textMuted: "#aab6c9", textInverse: "#151b27",
    brand: "#a6c0f5", brandText: "#15223a", brandSoft: "#242f43",
    operator: "#ffcfae", operatorText: "#2a1a10", operatorSoft: "#3a2a20",
    shadowTint: "0 0 0", highlight: "rgb(255 255 255 / 0.06)",
    accents: {
      peach: { soft: "#3a261c", mid: "#7a4a33", strong: "#ffc6a6" },
      lavender: { soft: "#2b2639", mid: "#52476f", strong: "#d1c4f1" },
      mint: { soft: "#1b332b", mid: "#376b55", strong: "#afe6ca" },
      sky: { soft: "#1b2a40", mid: "#395881", strong: "#aec8fa" },
      rose: { soft: "#3a1d27", mid: "#77384b", strong: "#f6b3c2" },
      butter: { soft: "#342b12", mid: "#6e5a20", strong: "#f2dc94" },
      slate: { soft: "#252d3a", mid: "#465365", strong: "#d3dceb" },
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
export const layout = { sidebar: "248px", sidebarCollapsed: "72px", column: "880px", panel: "440px", gutter: "16px" } as const;

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
