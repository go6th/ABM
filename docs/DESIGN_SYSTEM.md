# Design system: pastel on white, designed dark

This is the single design system for every screen in both route groups: client sign-in and
workspace, and operator sign-in and console. It replaces the earlier Evidence styles
(`design/ws/ws.css`), the Liquid Glass and Claude-styled layers, and the original
`design/*` tokens. All of those are deleted (see `docs/UI_REMOVED.md`).

## Files

| File | Holds |
|---|---|
| `web/ui/tokens.css` | Every colour, material, elevation, type, space, radius and motion token; the light, dark, system, increased-contrast and reduced-transparency blocks |
| `web/ui/base.css` | Reset, body type, focus ring, type role classes, theme-switch transition, page entrance |
| `web/ui/components.css` | Primitives (buttons, fields, segmented, switch, badges, groups, tiles, tables, banners, empty, skeleton, meters, dialog, sheet, menu, tooltip, toast, dropzone, steps, charts, grade mark, citation) |
| `web/ui/shell.css` | The client and operator shells, plus workspace pieces (composer, thread, rows, account, conversations, settings, evidence, deep dive) |
| `web/ui/auth.css` | Sign-in pages and their CSS 3D scenes |
| `web/ui/primitives.tsx` | Server-safe React primitives: `PageHead`, `Section`, `Badge`, `Banner`, `EmptyState`, `LoadError`, `Meter`, `Tile`, `PageSkeleton`, `Field`, `Kbd`, `Monogram` |
| `web/ui/ThemeToggle.tsx`, `web/ui/theme.ts` | Light, Dark or System |
| `web/ui/Scene.tsx` | The pointer-tilted 3D stage on the sign-in pages |
| `web/lib/messages.ts` | Every user-facing string of the client screens (one module, checked by the copy lint) |

`/__design` (development builds only) renders every primitive in both modes.

## Colour

Pastels are for surfaces, washes and marks. Text and icons use the matching deep ink; pastel
text never sits on white. `tests/unit/ui-tokens.test.ts` computes the contrast of every text
pair in both modes (at least 4.5:1) and checks that the system-dark block equals the dark block.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--canvas` | #FFFFFF | #0B0B0F | Page |
| `--surface` | #FFFFFF | #16161B | Cards, groups, tables |
| `--elevated` | #FFFFFF | #1F1F26 | Dialogs, sheets, menus |
| `--grouped` | #F5F5F7 | #1C1C22 | Grouped backgrounds, table heads, search |
| `--ink` | #1D1D1F | #F5F5F7 | Text |
| `--ink-2` | #6E6E73 | #A1A1A6 | Secondary text |
| `--primary` | #4A57D6 | #8E9BFF | One primary action per view, links, focus |
| Pastel families | `peri`, `mint`, `peach`, `lilac`, `rose`, `butter`, each with `-fill`, `-strong` and `-ink` | Dark fills are 16% washes; inks are lightened | Badges, tiles, banners |

Meaning by family:
- **peri:** information and the brand.
- **mint:** success and done.
- **peach:** the web, and anything that spends credits.
- **lilac:** people and the operator plane.
- **rose:** destructive actions and errors.
- **butter:** warnings and pending items.

## Type

The system font stack comes first: SF Pro on Apple devices, then a self-hosted Inter variable font
with a metric-matched fallback, so the swap does not shift layout. The scale follows the HIG:

| Role | Size and leading |
|---|---|
| Large title | 34/41 |
| Title 1 | 28/34 |
| Title 2 | 22/28 |
| Title 3 | 20/25 |
| Headline | 17/22 |
| Body | 15/22 |
| Reading | 16/26 |
| Callout | 16/22 |
| Subhead | 14/20 |
| Footnote | 13/18 |
| Caption | 12/16 |

Numbers use tabular figures.

## Shape, space, elevation

- **Radii:**

  | Radius | Size | Used for |
  |---|---|---|
  | mark | 8 | small marks |
  | control | 12 | buttons, inputs |
  | group | 16 | cards, groups, tables |
  | panel | 24 | dialogs, sheets |
  | stage | 28 | sign-in stage, home hero |
  | full | 999 | pills |

- **Space:** a 4px grid (`--s1` to `--s16`).
- **Elevation:** four periwinkle-tinted shadows (`--e1` to `--e4`); dark mode uses deeper,
  untinted shadows.
- **Materials:** translucent with blur (sidebar, toolbar, menus). Under reduced transparency
  they become opaque surfaces.

## Motion

- **Springs:**
  - Press: 120ms, scaling to 0.97.
  - Enter: 280ms, a spring sampled into `linear()`.
  - Exit: 180ms.
  - Theme switch: 250ms, colour only.
- **Page entrance:** the incoming page rises 8px and fades in.
- **Rows:** fade up as they appear.
- **Reduced motion:** every animation becomes a 120ms fade and every transition is instant.
  The 3D scenes stand still.
- Only `transform` and `opacity` animate, except the colour-only theme switch.

## 3D

The sign-in stage is CSS 3D: one perspective container, translucent slabs at different `translateZ`
depths, and a slow drift. The pointer tilts the rig by at most 6 degrees through two CSS variables
set with CSSOM; the CSP forbids inline style attributes but allows CSSOM. No WebGL is needed.
The client scene is pastel slabs (an account card, a grade ring, a signal list). The operator
scene is an isometric grid of periwinkle and lilac console tiles.

## Theme

`ai_theme` is a cookie set to `light`, `dark` or `system` (one year, `SameSite=Lax`, `Secure` on
https). The root layout reads it on the server and renders `data-theme` on `<html>`, so no script
runs before paint and nothing flashes. "System" leaves the attribute off, and the
`prefers-color-scheme` block applies. The toggle sits in the client sidebar, the operator sidebar
and on the sign-in pages. `theme-color` metadata follows the system scheme.

## Shells

- **Client:**
  - A translucent sidebar with a periwinkle wash, holding the brand, the client name, the
    navigation (`Workspace`, at most seven items) and the cap meter with the theme toggle.
  - A sticky toolbar material carrying the breadcrumb and the account menu.
  - Content columns: reading 680px, content 976px, wide 1280px.
  - Below 760px the navigation becomes a bottom tab bar.
- **Operator:** the same structure with a lilac wash and the operator navigation; the session
  line names the staff member and role.

## Copy rules

These rules are enforced by `web/tests/unit/ws-copy.test.ts`, which reads `lib/messages.ts` and the JSX text of every client and sign-in screen:
- Sentence case.
- No en or em dashes, and no spaced hyphens.
- No arrows on buttons.
- No all-caps labels and no middle-dot meta strings.
- No model or provider vendor names on client screens.
- "Archive record" for dead links.
- Client copy never names or implies another client.
