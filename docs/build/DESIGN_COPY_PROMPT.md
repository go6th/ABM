> Superseded where it conflicts by REPLICA_PROMPT.md (owner decision, 2026-10-14)

# Build prompt: make the web app match the Claude Design prototype

You are working in the Account Intelligence repository at `/home/sakib/ABM Generator`, on the web app in `web/` (Next.js 16 App Router, React 19, plain global CSS in `web/ui/*.css`, no Tailwind). The owner designed the product in Claude Design. Make the real app look, feel and flow like that design, screen by screen, while keeping the product decisions listed in section 2.

## 0. Sources of truth

1. **The design, as code:** `docs/design/claude-design/` holds the 17 screens unpacked from the owner's Claude Design export (`/home/sakib/Downloads/Account Intelligence by 6th.html`). Each file is the screen's HTML, its CSS and its interaction logic (a `class Component extends DCLogic` script with state and handlers; `sc-if` and `sc-for` are its conditionals and loops, `{{name}}` are bound values). Read them as the reference for markup, spacing, copy and behaviour. Do not ship them or their runtime; rebuild each screen as React components in `web/`.
   - User app: `01-home-chat`, `02-accounts`, `03-account-workspace`, `04-refresh-from-web`, `05-schedules`, `06-add-account`, `07-people`, `08-mail-and-drive`, `09-new-research`, `10-file-analysis`.
   - Admin console: `A1-admin-overview`, `A2-admin-clients`, `A3-admin-client-detail`, `A4-admin-users`, `A5-admin-library`, `A6-admin-runs-and-spend`, `A7-admin-platform-defaults`.
2. **The picture:** `/home/sakib/Downloads/Account Intelligence by 6th (2).pdf` (18 pages) is a rendered print of the same screens. Use it to check your result visually.
3. **The product rules:** `docs/build/BUILD_PROMPT.md`. It decides *what* the product does. This prompt decides *how it looks*. Where this prompt and section 8 of the build prompt disagree on visual style (font, colours, radius, shadows, sizes), this prompt wins; update section 8 and `docs/DESIGN.md` to describe the design system below and note "owner decision, 2026-10-13: match the Claude Design prototype". This prompt also supersedes `docs/build/VISUAL_PROMPT.md` (pastel and 3D); mark that file as superseded at its top and do not apply it.

Another session may be building in this repo. Check `git status` and `git log -3` first, work on a branch `feat/design-match` from the current build branch, and commit only if the owner has said you may.

## 1. Design system (copy exactly)

Put every value below into `web/ui/tokens.css` as CSS custom properties and use only the tokens elsewhere. These values were read from the design's CSS.

### 1.1 Font

- **Hanken Grotesk**, weights 400, 500, 600, 700, with `system-ui, sans-serif` fallback. Self-host it (download the woff2 files once into `web/ui/fonts/`, or use `next/font/google`, which downloads at build time and serves from the app). Never load it from `fonts.googleapis.com` at runtime: the CSP blocks it and it leaks visits to a third party. The font is licensed under the SIL Open Font License; keep the license file next to it.

### 1.2 Colours

| Token | Value | Used for |
| --- | --- | --- |
| `--bg` | `#F5F6F8` | Page background (both apps' content area) |
| `--surface` | `#FFFFFF` | Cards, active nav item, inputs, secondary buttons |
| `--ink` | `#101828` | Main text, user chat bubble background, logo tile, active filter chip |
| `--ink-2` | `#1D2939` | Answer paragraph text |
| `--text-3` | `#344054` | Chip and secondary-button text, table header text |
| `--text-nav` | `#475467` | Sidebar links, recent chats |
| `--muted` | `#667085` | Secondary text (`.mut`), labels |
| `--line` | `#E4E7EC` | Card borders, sidebar border, chip borders |
| `--line-soft` | `#EEF0F3` | Row dividers inside cards |
| `--line-input` | `#D0D5DD` | Input and secondary-button borders |
| `--hover` | `#ECEEF2` | Sidebar hover |
| `--hover-soft` | `#F2F4F7` | Chip fill (`.chipa`), rail hover, table header fill |
| `--row-hover` | `#F9FAFB` | Table row hover |
| `--accent` | `#2F4BE0` | Primary button, links, progress bars, chart bars, selected radio and checkbox, focus ring |
| `--accent-soft` | `#EEF1FF` | Citation chip fill |
| `--accent-wash` | `#F7F8FF` | Offer and source cards, selected option fill |
| `--accent-line` | `#C9D2FF` | Border of offer cards and selected options |
| `--accent-tint` | `#DDE3FF` | Avatar circle fill |
| `--logo-dot` | `#7C93FF` | Dot in the logo mark |
| `--admin-bg` | `#101828` | Admin sidebar |
| `--admin-on` | `#232F45` | Admin active nav item |
| `--admin-hover` | `#1B2638` | Admin nav hover |
| `--admin-text` | `#B6BDCB` | Admin nav text |

Grade and status pills (fill / text), always with the word written in the pill:

| Pill | Fill | Text |
| --- | --- | --- |
| Confirmed, Ready, Done, Active, Passed, Used | `#DCF5E8` | `#137A4B` |
| Probable | `#E6F0FF` | `#1D5FC4` |
| Hypothesis, warnings | `#FFF1D6` | `#8A5200` |
| Running, Scanning, Priority A | `#E0E7FF` | `#2F4BE0` |
| Internal, "From your files" | `#EDE9FE` | `#5B3FC4` |
| Idle, Not connected, Priority B, neutral | `#F2F4F7` | `#475467` |
| Failed, Access ended, destructive text | `#FEE4E2` | `#B42318` |
| Lead | pick a distinct, accessible pair (the design has none) and add it to this table |

Use one colour per grade everywhere (the PDF shows Probable as green on one page; the code says blue `#E6F0FF`/`#1D5FC4`; blue is correct).

### 1.3 Type scale

| Use | Size / weight |
| --- | --- |
| Hero heading (Ask empty state, if shown) | 36px / 700, letter-spacing -0.02em |
| Page title | 28px / 700 |
| KPI number (admin) | 30px / 700, letter-spacing -0.02em |
| Section heading in cards | 15px / 600 |
| Chat answer text | 16.5px / 400, line-height 1.7, colour `--ink-2` |
| User bubble, follow-up input | 16px |
| Main composer input | 17px |
| Body, table cells, nav | 14px |
| Secondary, chips, small tables | 13px |
| Meta, captions | 12px |
| Eyebrow labels (`THE ONE MINUTE VERSION`, `DEEP DIVE`, `SOURCES`, `RECENT`) | 11 to 12px / 700, uppercase, letter-spacing 0.04em, `--muted` |
| Pills | 11 to 12px / 700 |

### 1.4 Shape, depth, spacing

- Radius: 22px (main composer), 20px (follow-up composer), 18px 18px 4px 18px (user bubble), 16px (cards), 12px (buttons, workspace card), 10px (nav items, inputs, small buttons), 8px (recent links, segmented buttons), 6px (citation chips), 999px (chips and pills).
- Shadows (only these): composer `0 10px 32px rgba(16,24,40,0.07)`; follow-up composer `0 8px 26px rgba(16,24,40,0.06)`; raised cards `0 6px 22px rgba(16,24,40,0.05)`; active nav item and selected segment `0 0 0 1px #E4E7EC` or `0 1px 2px rgba(16,24,40,0.12)`.
- Layout: sidebar 248px with padding 20px 16px, gap 18px, right border `--line`; main content padding 36px 48px; chat column and composer max width 780px; page content max width about 1100px; table rows 60 to 68px high with 16px column gaps and 20 to 22px side padding.
- Spacing steps used by the design: 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 28, 32, 36, 48. Keep them; do not force an 8px grid onto this design.

### 1.5 Components (rebuild each as a reusable React component)

Copy the behaviour and look of the design's CSS classes:

- `.btn` primary (accent fill, white text, 44px, radius 12, weight 600) and `.btn2` secondary (white, `--line-input` border, hover turns border and text accent).
- `.chipb` suggestion chip (white, bordered, 40px, pill, hover accent), `.chipa` composer chip (grey fill `--hover-soft`, 32px, pill), `.fc` filter chip (36px, bordered pill; active filter chip is `--ink` fill with white text).
- `.card` (white, 1px `--line`, radius 16).
- `.nav a` sidebar link with 18px line icon (stroke 1.8, round caps), active state white with 1px ring; admin `.an a` on dark sidebar.
- `.cite` numbered citation chip (20px, radius 6, `--accent-soft` fill, accent text, underlined number; hover inverts). Clicking opens a source card under the answer (`--accent-wash` card with the number, title, meta and "Close").
- `.g` / `.pill` grade and status pills (section 1.2).
- `.row` / `.cr` / `.ur` / `.lr` / `.rr` grid table rows (CSS grid, `minmax(0, …fr)` columns, top divider `--line-soft`, hover `--row-hover`, whole row is a link with a `›` at the end).
- `.rail a` right-rail link with count (`DEEP DIVE` list).
- `table.xl` spreadsheet table for People, Jobs and Technology: 13px, sticky header with `#F2F4F7` fill and `#D9DDE4` borders, sortable header buttons, a grey row-number column, 36px rows, cell borders `--line`, row hover `#F7F9FF`, horizontal scroll inside its own frame, caption line like "People list.xlsx · Sheet 1 · 41 rows".
- `button.seg` segmented control (grey track, white selected segment with the small shadow), `button.sw` on/off switch (48 by 28 pill), `button.st` stepper, `input.f` / `select.f` form fields (44 to 48px, radius 10, `--line-input` border).
- `.kpi` admin metric card, horizontal bar rows for "What people ask about" and "Credits by purpose" style breakdowns, the vertical bar chart on Admin overview (accent bars with value labels on top; draw it as inline SVG or plain divs, no chart library).
- Logo mark: 28px `--ink` rounded square (radius 8) with a white chevron path and a `--logo-dot` dot (the SVG is in every screen file; reuse it exactly). Icons: the inline SVG line icons from the screens; no icon library.

### 1.6 Motion (as in the design)

The design is calm. Hover colour changes, the "Searching your workspace" state with a small accent dot (pulse it gently), answers appearing after the search state, the chat scrolling to the newest message. Keep transitions at about 150 to 200ms on colour, background and box-shadow. Respect `prefers-reduced-motion`.

## 2. Product decisions that change the design (the owner already decided these)

Build the design's look, but with these differences. Each is recorded in `docs/build/BUILD_PROMPT.md`.

1. **No caps or credits anywhere for clients.** Remove the sidebar "Accounts used 6 of 34" bar, "credits" on every user screen (Refresh, Schedules, New research, Add account), "Near credit limit" and "Waiting: credit limit" statuses, and in the admin console the account cap, credit pool, credit cap per pull and credit progress bars. The sidebar footer keeps only the user's avatar, name and email, plus Sign out. Admin screens may show usage numbers (units used, runs) as read-only reporting, never as limits.
2. **No 6th Library.** Do not build `06-add-account` (library grid) or `A5-admin-library`, and remove "6th Library" from the admin nav and "Publish from the 6th Library" from client detail. The Accounts page's main button opens New research (`09-new-research`), labelled "New research".
3. **No Mail and drive.** Do not build `08-mail-and-drive`; remove it from the nav, remove "From your mail and drive" from the Deep dive rail and "Mail and drive" from any "what this answer used" text and from the admin Features list.
4. **Composer chips:** the design's `Web: off` chip becomes a toggle chip **`Research: off` / `Research: on`** (see D3 of the build prompt). Keep `+ Upload a file` and the scope chip (`All 6 accounts`, or the account name on account pages). Add the **model picker** chip below the composer (Claude Opus 5.5 default, Claude Fable 5.1, Kimi K3; details in D3).
5. **Ask empty state:** the owner removed the heading and the starter questions. Show only the composer, centred vertically (the design's centred position), with the composer exactly as designed (22px radius, shadow, 17px input). After the first message, switch to the design's thread layout with the follow-up composer at the bottom.
6. **Chat answer:** build the thread as designed (navy user bubble, "Searching your workspace" state, 16.5px answer text, citation chips, the `FIRST CALL QUESTIONS`-style item card, the source card on citation click, follow-up chips). Do not add a "What this answer used" side panel. The design's "Want anything newer? … Refresh from web / Stay local" card becomes: "Want anything newer? Your workspace was refreshed 2 days ago." with buttons **Research this** (primary, resends with Research on) and **Stay local** (secondary, dismisses).
7. **Refresh from web (`04`)** keeps its layout (three option cards: Job postings only, People and teams, Full refresh, each with a time estimate) but replaces credits with a units range labelled "estimate", removes "Research credits left this month", and on confirm starts a research run and opens the Run view. "You will be notified when it finishes" stays.
8. **Schedules (`05`)** keeps its page and per-account segmented control (3 days, Weekly, 2 weeks, Monthly, Off) and "Next refresh in N days". Add an "Auto-continue scheduled runs" checkbox per account. Remove the credit estimate and pool footer. The account page's settings line (`03`) links here for that account.
9. **Account workspace (`03`)** keeps the header (breadcrumb, title, sub-line, Schedule and Refresh from web buttons), `THE ONE MINUTE VERSION` card, Key findings, "What changed since <date>" (shown only after a later refresh; on the first scan show the design's note "This is the first scan, so there is nothing to compare yet…"), the account-scoped composer, and the right rail `DEEP DIVE` with counts (Signals, People, Job postings, Tech stack and teams, News, Sources) and `FILES` with checkboxes, "Download selected (N)" and "Download all". Each rail item opens its table screen.
10. **People (`07`) and the other tables:** the spreadsheet table (`table.xl`) with filter chips, sort on every column, row numbers, sticky header, "Open in Excel" (downloads a real `.xlsx`). Sorting, filtering and paging are server-side (build prompt section 9.6). Use the same component for Jobs, Technology, News and Sources.
11. **Run view:** not in the design. Build it in the same style: page title, a card listing the skill's eight steps with Done / Running / Waiting pills, units used, files as they appear, Stop (secondary). The checkpoint card uses the offer-card style (`--accent-wash`, `--accent-line` border) with the four buttons from build prompt section 5 (Continue as primary, the rest secondary).
12. **Admin console:** build `A1` (Overview: KPI cards without "of 11,000 pooled", questions per week chart, what people ask about, not using it; isolation check pill), `A2` (Clients table: users, accounts, units this month as a number, last active, status; "Add client"), `A3` (Client detail: enable or disable, optional idle expiry with "Off" as the default option, "Allow non-Claude models" switch default off, members list; no caps, no library), `A4` (Users: user, client, accounts, questions (30d), last active, Access pill with "Disable" / "Re-enable" buttons), `A6` (Runs and spend: units by purpose, isolation check, runs table with Done / Running / Paused for approval / Failed pills; read-only), `A7` (Platform defaults: checkpoint size, model routing for Phase 1 and Phase 2, rate table, research skill version with upload, activate and roll back; safeguards as designed; admins list). Keep the dark navy sidebar and "View as a client" only if it shows a read-only view without client question text or files; otherwise remove it.

## 3. Accessibility and quality (must hold)

- Contrast 4.5:1 for text. Measured: `--muted #667085` on `--bg` 4.6:1 and on white 4.97:1 (pass); accent on white 6.55:1; grade pills 4.67 to 5.72:1 (pass). `--faint #98A2B3` on `--bg` is only 2.38:1 (fail): use `--muted` for the "RECENT" label and drop `--faint`. Check every pair with a script (`web/scripts/check-contrast.mjs`, run in `npm test`) and darken a token by the smallest step if a pair fails.
- Input borders `--line-input #D0D5DD` are 1.47:1 against white. Add a 3:1 cue for inputs (darker border on hover and focus, and a visible focus ring) so form fields are identifiable.
- Every control is a real `button`, `a` or `input` with a label; chips that change state use `aria-pressed`; the model picker is a menu button; tables have proper `th` and `scope`; the composer label is visually hidden but present (as in the design).
- Visible focus ring on every control: 2px `--accent` with 2px offset.
- Targets at least 40px high (the design already does this except `.chipa` at 32px; make clickable composer chips 40px tall on touch devices).
- Phone width (375px): sidebar becomes an overlay menu (Escape closes it, focus returns), the right rail moves under the main column, tables scroll inside their frame, no horizontal page scroll.
- Zero CSP console errors: build styles as classes in the CSS files, not inline `style` attributes (the design's markup uses inline styles everywhere; convert them to classes). Self-host the font.
- Empty, loading and error states for every screen in the same visual language (skeleton blocks on `--hover-soft`, error messages in a card with one next step).
- Every page has its own `<title>`.

## 4. How to work

1. Read section 0 sources. Open each design file and the matching PDF page side by side. List in `docs/build/DESIGN_MATCH.md` every screen, which repo route and components it maps to, and which section 2 change applies.
2. Build in this order: tokens and font; shell (user sidebar, admin sidebar, mobile menu); shared components (section 1.5); Ask (empty, thread, composer, citations, source card, offer card); Accounts; Account workspace; tables (People, Jobs, Technology, News, Sources); New research and File analysis; Refresh and Schedules; Run view; admin console.
3. After each screen, start the app, sign in as the seeded demo user, take screenshots at 1360px and 375px into `web/test-results/design-match/` (gitignored), and compare with the matching PDF page. Fix spacing, sizes and colours until they match, except where section 2 says otherwise.
4. Tests: Playwright plus axe on every screen at both widths (no violations, no CSP errors, no horizontal scroll); the contrast script; existing unit tests still pass; add component tests for the composer toggle, model picker, citation chip and spreadsheet table sorting.
5. At the end report: screens done, side-by-side screenshot location, every place you deviated from the design and why, test results, and decisions you need from the owner.
