# Visual notes: pastel colour, depth and motion (2026-10-13)

The owner decided on 2026-10-13 to replace the visual rules of `BUILD_PROMPT.md` section 8 with a pastel scheme that has real depth and motion. Layout, screens, routes, copy and data flow stay as they are. Work happens on branch `feat/visual-pastel-3d`, cut from `feat/final-product` at `3206a9e`.

## What was there before

| When | Commit | What | Why it went |
| --- | --- | --- | --- |
| up to 2026-10-10 | (many) | "Liquid Glass" and Claude-styled UI. Pieces: `components/motion/EvidenceOrb`, `LiquidGlass`, `TiltCard`, `useSpring` | Removed in `b865997` ("remove the glass and claude-style UI") in favour of the "Evidence" workspace system: spruce, ochre, brick |
| 2026-10-11 | `bac4e43`, `0e92c72` | Pastel design system: 6 families (peri, mint, peach, lilac, rose, butter) with fill, strong and ink steps; light and dark; self-hosted Inter; Apple HIG type scale; radii 8/12/16/24/28; CSS 3D sign-in stages (`ui/Scene.tsx`, pointer tilt of at most 6 degrees through CSS variables set with CSSOM) | Replaced by the minimal rules of `BUILD_PROMPT.md` section 8 (system font, 6px corners, no shadows, no gradients, motion only for progress), now in the uncommitted part of snapshot `3206a9e`. No commit message gives a reason beyond that rule set. |

## What I reuse

- **The CSSOM tilt technique from `ui/Scene.tsx`.** The CSP allows `element.style.setProperty` from JavaScript but blocks `style=""` attributes. The pointer writes `--rx` and `--ry`, and the stylesheet does the transform.
- **The colour rule from the 11 October system:** pastels are for surfaces and marks, deep "ink" shades are for text, and pastel text never sits on white.
- **Not reused:**
  - **Inter webfont:** section 8 keeps the system stack.
  - **The HIG type scale:** the scale stays 15/13/22/28.
  - **Dark mode:** not asked for. The current tokens have no dark block either.
  - **The legacy `Scene.tsx` rigs:** the new scenes are WebGL. `Scene.tsx` stays on the deletion list (A5).

## Findings from the "before" pass

- **Screenshots:** `web/test-results/visual-before/`, 23 screens at 1280px and 375px. The account is `pilot-a-1.example` and the run is `efe58555`. Taken with `web/scripts/visual-shots.mjs`.
- **CSP in the dev server:** every page logs CSP violations, even sign-in. All of them on sign-in come from Next's dev tools, which inject an un-nonced `<style>` and `style` attributes. The zero-violation check therefore runs against a production build (`next build && next start`), not `next dev`.
- **Operator console:** opened locally through the dev staff verifier, with cookie `6th_operator_session=operator:dev-operator`, `APP_ENV=dev` only.
- **Titles:** every page has the same `<title>`. Fixing that is in the main build (A6), not here.

## Plan

1. **Tokens and contrast check.**
   - In `ui/tokens.css`:
     - page, surface and tint surfaces
     - five hues (lavender, sky, mint, peach, butter) plus rose for failure, each with steps -50, -100, -200 and -ink
     - lead hue for actions, ink texts, input border at 3:1
     - grade and state pairs
     - gradients, elevation, radii 10 and 16, motion durations and easing
   - **No colour literal outside `tokens.css`,** enforced by the contrast script.
   - `web/scripts/check-contrast.mjs` parses `tokens.css` and the CSS rules, finds every text-on-surface pair that is actually used, and fails below 4.5:1 for text and 3:1 for input borders and large text. Wired into `npm test`.
2. **Elevation, radius, glass.**
   - `--elev-1..3` are tinted multi-layer shadows on cards, composer, dialogs, menus and the checkpoint card.
   - Cards rise on hover; buttons lower on press.
   - The sidebar, composer and dialogs get glass panels, with a solid fallback under `@supports not (backdrop-filter)`.
   - Tables stay flat.
3. **Motion.**
   - **CSS transitions and keyframes** for:
     - hover and focus
     - the page entrance (fade and an 8px rise of the content column)
     - the step fill-in and the running-step pulse
     - skeleton shimmer
   - **`motion` 13.4.4** only for elements created on the client after hydration:
     - new chat turns
     - the "Run deep search" button and the checkpoint card
     - the sliding highlight of tabs and the Normal | Deep control
     - the mobile menu and toasts
   - Under reduced motion, only opacity fades under 150ms remain.
4. **CSP.**
   - The root layout reads `x-nonce` and passes it to a client provider. That provider calls `setNonce` from `get-nonce`, which the Radix Dialog scroll lock uses, and sets `MotionConfig nonce`.
   - `motion` never renders server-side initial styles: animated pieces start from CSS classes or mount after hydration.
   - `app/not-found.tsx`, `app/error.tsx` and `app/global-error.tsx` replace Next's defaults, which use inline styles.
   - No `'unsafe-inline'`.
5. **Tilt.**
   - Applied to:
     - the starter questions on Ask
     - the Accounts empty state
     - the checkpoint card
     - the drop zone
     - the New research tabs
   - At most 6 degrees, eased, springing back, with a pointer-following highlight.
   - Never on anything that holds a table or a field being typed in. Off on touch and under reduced motion.
6. **WebGL.**
   - The scene appears behind `AuthFrame` and behind the Ask empty state: a few soft pastel blobs drifting, with pointer parallax.
   - Packages: `three` 0.186.1, `@react-three/fiber` 9.8.1, `@react-three/drei` 10.7.9.
   - Loaded with `next/dynamic` `ssr: false`. A static gradient paints first.
   - Pixel ratio is capped at 1.5. The scene pauses when hidden or off screen.
   - It falls back to the static gradient when:
     - WebGL is missing
     - the device has few cores
     - frames take more than 20ms for 2 seconds
   - It fades to a still frame while the user types.
7. **Admin console.** Same tokens and elevation; page fades and skeletons only.
8. **Tests and docs.**
   - Playwright with axe on every screen, both widths, with and without reduced motion. The run checks:
     - CSP console errors
     - horizontal scroll
     - reduced motion
     - WebGL off
   - Also: per-route bundle sizes before and after, the contrast script, and the existing unit tests.
   - `BUILD_PROMPT.md` section 8 and `docs/DESIGN.md` are rewritten to the new rules, with the owner's decision recorded.
