> Superseded where it conflicts by REPLICA_PROMPT.md (owner decision, 2026-10-14)

# Visual upgrade prompt: pastel colour, 3D depth and motion

You are working in the Account Intelligence repository at `/home/sakib/ABM Generator`, on the web app in `web/` (Next.js 16 App Router, React 19, plain global CSS in `web/ui/tokens.css`, `base.css`, `components.css`, `shell.css`, `auth.css`; no Tailwind). The owner wants the product to keep its simple layout but look richer: a pastel colour scheme, real 3D depth and smooth, professional animation. Build that.

## 0. Scope and what this prompt overrides

- This prompt **replaces the visual parts of section 8 of `docs/build/BUILD_PROMPT.md`**: palette, shadows, gradients, motion and decorative depth. Edit section 8 and `docs/DESIGN.md` so they describe the new system, and say in both that the change was the owner's decision on 2026-10-13.
- These rules from section 8 **still apply and must not get worse**: system font stack, type scale (15/13/22/28), sentence case, one primary action per screen, controls at least 40px high, visible keyboard focus, text contrast at least 4.5:1 (3:1 for borders of inputs and for large text), no horizontal page scroll at 375px, every screen has empty, loading and error states, zero CSP console errors.
- Layout, screens, routes, copy and data flow do not change. This is a styling and motion pass, not a redesign. Data tables (People, Jobs, Technology, Sources, Accounts) stay flat and dense: depth goes on surfaces around them, never inside rows.
- Another build session may be working in this repo. Before you start, check `git status` and `git log -3`. Work only on a branch named `feat/visual-pastel-3d` created from the current build branch, and touch only `web/` and the two design docs. Commit only if the owner has said you may.

## 1. Understand first

1. Read `web/ui/*.css`, `web/app/layout.tsx`, `web/app/(client)/layout.tsx`, `web/components/ws/WorkspaceShell.tsx`, `Ask.tsx`, `Composer.tsx`, `Conversation.tsx`, `RunSteps.tsx`, `AccountRows.tsx`, `AccountHeader.tsx`, `AccountTabs.tsx`, `TableScreen.tsx`, `FilesTab.tsx`, `web/app/(client)/research/new/page.tsx`, `web/ui/AuthFrame.tsx`, the operator console under `web/app/operator/`, and `web/proxy.ts` (the CSP).
2. Look at the earlier attempt before you design anything: the git history of `docs/DESIGN.md` and `docs/DESIGN_SYSTEM.md` (an older pastel system with a 3D sign-in) and the legacy `web/ui/Scene.tsx`. Write down in `docs/build/VISUAL_NOTES.md` what was there, why it was removed if the history says so, and what you will reuse.
3. Start the stack, sign in as the seeded demo user, and screenshot every screen at 1280px and 375px **before** you change anything, into `web/test-results/visual-before/` (gitignored). You will compare against these at the end.

## 2. Colour system (pastel)

Define everything as CSS custom properties in `web/ui/tokens.css`. No colour literal may appear outside that file.

- **Base surfaces:** a very light warm or cool off-white page background (for example `#FAF9FF`), white raised surfaces, and one soft tinted surface for the sidebar and composer.
- **Five pastel hues** as surface tints, each with three steps: `-50` (wash), `-100` (fill), `-200` (border or highlight). Suggested hues: lavender, sky, mint, peach, butter. Tune the exact values yourself.
- **An "ink" version of each hue** (a deep shade of the same hue) for text, icons and links on pastel surfaces. Pastels cannot carry white text, so primary buttons use a mid-to-deep shade of the lead hue (for example a periwinkle around `#5B5BD6`) with white text, checked at 4.5:1.
- **One lead hue** (lavender or periwinkle) for the primary button, links, focus rings and the active nav item. The other hues are for surfaces, illustrations and the 3D scene, not for actions.
- **Text:** one near-black ink with a slight hue (for example `#1E1B2E`), one secondary grey at 4.5:1 or better on every surface it appears on.
- **Status and grades:** pastel fill plus ink text plus a text label, never colour alone. Confirmed = mint, Probable = sky, Hypothesis = butter, Lead = peach, Internal = lavender, Failed = a soft rose with deep rose text. Run states reuse the same set. Fix the prototype's inconsistency (Probable was green on one page and blue on another): one hue per grade everywhere.
- **Gradients:** allowed, but only soft two- or three-stop pastel gradients on large surfaces (page background, auth panel, empty states, the hero area of Ask). Never on text, buttons or table cells.
- Write `web/scripts/check-contrast.mjs` that reads `tokens.css`, computes WCAG contrast for every text-on-surface pair the CSS actually uses, and fails below the thresholds. Run it in `npm test`.

## 3. Depth and 3D

Use three levels of depth, from cheapest to richest. Choose the cheapest that achieves the effect.

1. **Layered shadows (everywhere):** define `--elev-1`, `--elev-2`, `--elev-3` as soft, tinted, multi-layer shadows (two or three layers, low opacity, tinted with the lead hue, never pure black). Cards, the composer, dialogs, the mobile menu and dropdowns get elevation. Hover raises a card one level; pressing a button lowers it (translateY 1px and a smaller shadow). Increase the corner radius from 6px to a scale of 10px (controls) and 16px (cards and panels); update section 8 to match.
2. **CSS 3D transforms (interactive surfaces):** cards on the Accounts empty state, the starter questions on Ask, the checkpoint card, the file drop zone and the New research tabs may use `perspective` and a subtle pointer-following tilt (at most 6 degrees, eased, spring back on leave), with a soft specular highlight that follows the pointer. Never tilt anything that contains a table or a form field the user is typing in. Glass panels (`backdrop-filter: blur()` over the pastel background) are allowed for the sidebar, the composer and dialogs, with a solid fallback where `backdrop-filter` is unsupported.
3. **WebGL scene (two places only):** a real 3D scene behind the sign-in pages (`AuthFrame`) and behind the empty state of Ask. Use `three` with `@react-three/fiber` and `@react-three/drei`. Content: a few soft pastel shapes (rounded blobs or glass spheres) floating slowly with gentle parallax on pointer move and soft lighting; nothing literal, no logos, no text in 3D. Requirements:
   - Load it with `next/dynamic` and `ssr: false`, only on those two screens, so no other page pays for it. Report the added bundle size per route.
   - Render a static pastel gradient first (same colours) so the page looks finished before WebGL loads and when it fails.
   - Cap device pixel ratio at 1.5, pause rendering when the tab is hidden or the canvas is off screen, and fall back to the static gradient when WebGL is unavailable, when `navigator.hardwareConcurrency` is low, or when frame time stays above 20ms for two seconds.
   - The scene must never sit on top of text or controls, must be `aria-hidden`, and must not move once the user starts typing in the composer (fade it to a still frame).

## 4. Motion

Use the `motion` package (Framer Motion) for component animation and plain CSS transitions for hover and focus. Pin an exact, already-released version.

- **Durations and easing as tokens:** `--dur-fast 120ms`, `--dur-base 200ms`, `--dur-slow 320ms`, and one or two easing curves (a gentle ease-out and a soft spring). Nothing slower than 400ms except the 3D scene's idle drift.
- **Where motion goes:**
  - Page and tab changes: a short fade and 8px rise of the content column. The sidebar never moves.
  - Ask: new user and assistant turns slide in; streamed text appears without layout jumps; citation chips pop in after the sentence finishes; the "Run deep search" button and the checkpoint card enter with a soft spring.
  - Run view: the eight steps fill in one after another; the running step has a gentle pulse; units used counts up; files slide into the list as they appear.
  - Tabs and the Normal | Deep control: a sliding highlight under or behind the active option.
  - Loading states: pastel skeleton blocks with a slow shimmer instead of spinners, except the run progress indicator.
  - Mobile menu: slides in over the page with a dimmed pastel backdrop; closes on Escape (already required).
  - Toasts and saved confirmations ("Schedule saved"): fade and rise, auto dismiss.
- **Never animate:** table rows when sorting, filtering or paging; numbers in tables; anything while the user is typing; layout properties (animate only `transform` and `opacity`).
- **Reduced motion:** when `prefers-reduced-motion: reduce` is set, turn off tilt, parallax, the WebGL animation (show a still frame), slides and springs; keep only opacity fades under 150ms. Test this.

## 5. CSP: this is the trap

`web/proxy.ts` sets `style-src 'self' 'nonce-…'` with no `'unsafe-inline'`. Style **attributes** in server-rendered HTML are blocked; changes made from JavaScript through `element.style` are allowed.

- `motion` components render their initial styles as a `style` attribute during server rendering, so they are blocked on first paint and the page flashes or appears unstyled. Fix this one way, consistently: set initial states with CSS classes and let `motion` animate on the client only (for example `initial={false}` on server-rendered content, or wrap animated pieces in client components that mount after hydration), or pass the per-request nonce through to every `<style>` the libraries inject (read `x-nonce` in the root layout; call `setNonce` from `get-nonce` for libraries that use it). Do not add `'unsafe-inline'`.
- `@react-three/fiber` sets canvas size through `element.style` (allowed). Check it anyway.
- Acceptance: zero CSP violations in the browser console on every screen, in light motion and reduced motion.

## 6. Accessibility and performance budgets

- Keyboard focus ring: 2px lead-hue ring plus a 2px white gap, visible on every surface including glass and gradients.
- Contrast script (section 2) passes; axe passes on every screen.
- Layout shift (CLS) under 0.05 on every screen; no layout shift from streamed answers.
- JavaScript added to routes other than sign-in and Ask: under 25 KB gzipped. Report the WebGL routes separately.
- Smooth 60fps on a mid-range laptop for hover, tilt and page transitions (check with the browser performance panel and record one trace per screen type).
- Works at 375px: no horizontal scroll, tilt disabled on touch devices, WebGL scene simplified (fewer shapes, lower pixel ratio).

## 7. Admin console

Apply the same tokens and elevation to `web/app/operator/`, but calmer: no WebGL, no tilt, motion limited to page fades and skeletons. The console is a working tool.

## 8. Tests and acceptance

1. Screenshots of every screen at 1280px and 375px, light motion and reduced motion, saved to `web/test-results/visual-after/` (gitignored), compared side by side with the "before" set in `docs/build/VISUAL_NOTES.md`.
2. Playwright with axe on every screen: no violations, no CSP console errors, no horizontal scroll at 375px.
3. A Playwright test that emulates `prefers-reduced-motion: reduce` and checks that the WebGL canvas is still and no element uses a transform animation.
4. A test that disables WebGL and checks that sign-in and Ask still render the static gradient and work.
5. `check-contrast.mjs` passes. All existing web unit tests still pass.
6. Bundle size report per route before and after.

## 9. How to work

1. Section 1 first, then a short plan appended to `docs/build/VISUAL_NOTES.md`, then build in this order: tokens and contrast script; elevation, radius and glass; motion tokens and component motion; CSP fix; tilt; WebGL scenes; admin console; tests and docs.
2. Verify in the browser after each step at both widths. Fix what does not look right before moving on.
3. At the end, report: what changed, screenshots location, bundle sizes, test results, anything you chose not to do and why, and decisions you need from the owner (exact pastel values, whether the WebGL scene should also appear on the Accounts empty state).
