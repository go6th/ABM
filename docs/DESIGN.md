# Workspace design system: Evidence

The client workspace (`web/app/(client)`) is drawn with one small system, defined in
`web/design/ws/ws.css` and used through the components in `web/components/ws/`. Every
user-facing string of those screens lives in `web/lib/messages.ts`.

## Point of view

Sellers use the workspace to prepare a first call, so they need to trust a claim in seconds.
Evidence is the interface: every claim carries a grade and a citation. The grade mark, the
citation chip and the evidence drawer are the one bold element. Everything around them stays quiet.

Each colour has one meaning:

| Colour | Means |
|---|---|
| Spruce | Evidence, held here (local and free), the primary action |
| Ochre | Leaves the workspace or spends credits (web refresh, credits) |
| Brick | Errors only |

Nothing else is coloured.

## Tokens

The values are the ones in the build prompt, section 3.2, used unchanged. `web/tests/unit/ws-tokens.test.ts`
reads them from the stylesheet and checks contrast in both themes: text at least 4.5:1, marks at
least 3:1. Four tokens were added, each checked by the same test:

| Token | Light | Dark | Why |
|---|---|---|---|
| on-spruce | #FFFFFF | #0B1A16 | Text on spruce buttons (dark text in dark mode, as 3.2 asks) |
| on-ochre | #FFFFFF | #1E1607 | Text on the ochre confirm button |
| brick-tint | #F8E6E2 | #3A1E1A | Ground of error banners |
| hover | #EAEEEA | #1F2A32 | Hover and pressed ground of quiet controls |

Dark mode follows the system setting. A `data-theme` attribute on `.ws` can force either theme;
the gallery uses it.

## Type

- **Hanken Grotesk** for the interface. Body is 14/21, small 12.5/18, labels 13/18 at 500 and
  titles 20/26 at 600, with tabular numerals.
- **Source Serif 4** for what is read as prose: account names and page titles (clamped 26 to
  34px), answers (18/29), "What we know" claims, and source titles and excerpts.
- Both fonts are self-hosted from `@fontsource-variable` and bundled, so the CSP keeps
  `font-src 'self'`.

## Shape and motion

- **Radii by role:** 3px for chips and marks, 8px for controls and the web offer, 12px for
  the composer and floating layers.
- **Shadows:** only on layers that float (the drawer, dialogs and the user menu). Everywhere
  else uses borders and spacing.
- **Lists:** rows with dividers, not grids of cards.
- **Motion:** only motion that answers an action. The drawer slides in over 180ms, the "What
  this answer used" chevron rotates, and chips settle once an answer finishes. All of it is off
  under `prefers-reduced-motion`.
- **No inline style attributes:** the CSP has no `'unsafe-inline'` for styles. The cap meter
  and progress slices are drawn with SVG attributes and data attributes instead.

## Layout

```
+------------------------------------------------------------------------------+
| Account Intelligence by 6th | Client name                 [==--] 3 of 10  (TU)|
+-----------+------------------------------------------------------------------+
| Home      |            Halden Medical Group             [Refresh from web]   |
| Accounts  |            Refreshed 6 October                                    |
| Conversa. |            Overview People Job postings Tech Sources Signals      |
| Alerts    |            What we know                                           |
| Runs      |            (o) Confirmed  claim in serif                          |
|           |            What changed                                           |
|           |            Downloads                                              |
| Settings  |            [ Ask about Halden Medical Group                (^) ]  |
+-----------+------------------------------------------------------------------+
```

- **Home:** the title, a large question box across all accounts, then "Since you last looked"
  (at most five signals).
- **Accounts:** a title and count, search with a two-way sort, the rows, then Add account.
- **Account workspace:** the title and state, Refresh from web (the screen's ochre action), a
  row of deep dive links, then What we know, What changed, Downloads and the composer.
- **Conversations:** the threads on the left, then the reading column with the composer docked
  at the bottom.
- **Mobile (760px and below):** the rail becomes a bottom bar, the top bar drops the
  "by 6th" tag and the meter text, and rows stack.
- **Evidence drawer:** a 392px side panel, which becomes a bottom sheet at 1100px and below.

## Defaults rejected (section 3.7)

The previous workspace used several of these defaults; all were removed from the rebuilt screens:

- the orb and glass hero, gradient washes and hover lifts;
- tracked all-caps eyebrow labels ("RESEARCH WITH CONFIDENCE", "CHATS");
- middle-dot meta strings ("Enter to send · Shift + Enter");
- arrows in buttons;
- Inter as the default face;
- identical rounded cards with the same soft shadow (Home's three stat tiles became nothing,
  because the cap meter and the rows carry the same facts).

The copy lint (`web/tests/unit/ws-copy.test.ts`) keeps the text-level tells out.

## Gallery

`/__design` exists only in development (`page.dev.tsx` with `pageExtensions` in
`next.config.ts`). It shows every signature component and state in light and dark. A production
build has no such route; checked with `grep` over `.next` after `next build` (zero matches for the
gallery's sample text).
