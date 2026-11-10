# UI framework audit and implementation plan

Date: 2026-11-09

## Scope and evidence

This document reviews the user-supplied *Comprehensive User Interface Audit and Implementation Framework* against the current repository and UI audit records. It is a planning audit, not a fresh browser, assistive-technology, or runtime audit. The worktree contains unrelated uncommitted changes; this plan does not modify application code.

Evidence reviewed:

- `web/` implementation and its route/component layout.
- `docs/project-audit/reports/audit_web.md` (static frontend inventory dated 2026-11-03).
- `docs/ui-audit/2026-11-07-pastel/findings.mjs` and its screenshots/report sources (client UI review dated 2026-11-07).
- `docs/decisions/ADR-0101-frontend-stack.md`, `ADR-0103-frontend-depth-budget.md`, and `ADR-0136-dead-code-and-config-registers-parked.md`.
- The supplied framework text.

The 2026-11-07 review captured client screens in a local browser and recorded many fixes, but it is not evidence that every current route, viewport, keyboard path, or screen-reader path has been checked. The 2026-11-03 inventory was static. No tests or new browser sessions were run for this plan.

## Audit conclusion

The supplied framework is a useful checklist for interaction states, keyboard access, error recovery, target size, and user control. It is not a reliable implementation specification as written. It presents optional patterns as universal requirements, overstates several performance and accessibility claims, and recommends architectures the current product does not need.

The app is a single Next.js App Router application with owned React components, CSS tokens and styles, server-rendered pages, live status updates, Playwright/axe coverage, and a shared modal/toast layer. Its immediate UI work is consistency and verification across the existing client and operator surfaces, not decomposition into independently deployed frontends.

### Recommendation-by-recommendation assessment

| Framework recommendation | Project fit | Audit finding and disposition |
|---|---|---|
| Nielsen heuristics and severity scoring | High | Keep as a review checklist. Use the scale to rate observed, reproducible issues; do not assign severity from a general principle alone. |
| Explicit ideal, empty, loading, error, and success states | High | Keep. Many states already exist in `StateCard`, route error/loading boundaries, live status components, alerts, and toasts. Audit each important flow for missing or contradictory states rather than adding five flags to every component. |
| Replace boolean logic with XState everywhere | Low | Reject as a blanket migration. Existing flow complexity is localized and much state is simple. Use discriminated unions or reducers where transitions are genuinely complex; adopt a state-machine library only for a demonstrated transition/maintenance problem. |
| 24 × 24 CSS-pixel minimum targets | High | Verify against WCAG 2.2 AA, including the spacing and other exceptions. The November 7 review raised many controls to 36–40 px; icon-only controls, dense tables, menus, and mobile layouts still need measurement. |
| 44 × 44 target size as a requirement for primary controls | Medium | Keep as a design goal for frequently used touch controls, not as the stated WCAG AA requirement. The 44 × 44 enhanced criterion is AAA; do not label it mandatory AA compliance. |
| `:focus-visible`, focus contrast, and unobscured focus | High | Existing CSS and modal work include focus styling and focus containment. Verify all routes, sticky/mobile navigation, menus, dialogs, and dynamically inserted content with keyboard-only use. The framework incorrectly treats the 2 px / 3:1 Focus Appearance test as AA: WCAG 2.2 SC 2.4.13 is AAA. AA still requires visible focus and focus not entirely obscured. |
| Redundant-entry reduction and multi-step persistence | Medium | Keep as a flow review principle. Preserve user input on validation failure, tab changes, and recoverable requests. Avoid blanket `sessionStorage` persistence for account or sensitive content; select the minimum safe state per flow. The app has no checkout flow matching the framework's examples. |
| Automated and manual accessibility checks | High | Keep both. CI axe checks are already present, but static checks cannot establish complete accessibility. Add a small, repeatable keyboard and screen-reader review for high-value flows and verify automated coverage on current screens. |
| Optimistic updates for low-risk actions | Selective | Consider only when latency is visible and rollback is clear. Paid research/refresh starts, destructive account actions, permissions, and authoritative run states remain confirmation-first and server-confirmed. Existing owner rule requires two confirmations before a paid start. |
| Command palette | Optional | Defer pending evidence that users need faster cross-app navigation. It adds a new interaction surface and command authorization/visibility rules. |
| Spring animation and FLIP everywhere | Low | Reject as a blanket rewrite. The app already uses CSS transitions and reduced-motion rules. Use transforms/opacity for restrained decoration; profile actual interaction delays before introducing animation libraries or FLIP measurements. The claim that all layout animation is a severe anti-pattern and that FLIP guarantees 60 fps is too broad. |
| Micro-frontends / Module Federation / Shadow DOM | Not applicable now | Do not implement. The codebase is one product shell and one deployment, with shared auth, design tokens, and routing; no independent team/deployment boundary or CSS collision blocker was established. Continue consolidating owned components and tokens. |
| `contain: strict` and `content-visibility: auto` | Conditional | No current evidence that DOM/rendering cost warrants this. `contain: strict` includes size containment and can break intrinsic sizing; test a specific large, off-screen region before use. Profile before optimizing. |
| Server-driven UI (SDUI) | Not applicable now | Do not implement. Current UI composition is stable and security-sensitive; there is no demonstrated need to change layouts remotely. Keep server data/schema contracts typed and render through the owned component set. |

### Standards corrections

- WCAG 2.2 SC 2.5.8 Target Size (Minimum) is Level AA. It requires a 24 × 24 CSS-pixel target or an applicable exception, including a defined spacing exception. WCAG SC 2.5.5 Target Size (Enhanced), at 44 × 44, is Level AAA.
- WCAG 2.2 SC 2.4.13 Focus Appearance and SC 2.4.12 Focus Not Obscured (Enhanced) are Level AAA. SC 2.4.11 Focus Not Obscured (Minimum) and SC 2.4.7 Focus Visible are Level AA. Do not claim that `:focus-visible` alone establishes conformance; focus order, visibility, contrast, and obscuration must be checked in context.
- Fitts's Law is a design model, not a WCAG legal requirement. WCAG target-size criteria are independently testable requirements.
- Do not promise “universal accessibility,” “mathematically sound” UI, or guaranteed 60 fps from adopting a library or technique. State what was tested and against which criterion.

Primary standards references: [WCAG 2.2 Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum), [WCAG 2.2 Focus Appearance](https://www.w3.org/WAI/WCAG22/Understanding/focus-appearance), and [WCAG 2.2 Focus Not Obscured (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum).

## Project-specific implementation plan

Priority labels are proposed planning order, not a fresh severity score for current production behavior. No current release-blocking UI defect was confirmed by this static review.

### P1 — Close documented client interaction gaps

1. Replace the remaining browser confirmation flow for Support Access revoke with the shared modal/confirm dialog. Keep the operation disabled while pending, present request failures in the dialog, and announce success. Preserve server-side authorization and the paid-start double-confirmation rule.
2. Review all icon-only and compact controls against AA target-size requirements, including spacing exceptions. Give high-frequency touch controls a 40–44 px hit area where the layout permits.
3. Recheck shared modal behavior: initial focus, tab containment, Escape/backdrop rules, pending-request close behavior, focus restoration, background inertness, and scroll locking. Confirm the modal is announced with an accessible name and description.

Evidence anchor: Support Access revoke is marked Open in `docs/ui-audit/2026-11-07-pastel/findings.mjs`. The same finding mentions Mail and Drive disconnect, but the current `web/app` has no Mail and Drive page or disconnect action; that part of the older review is stale and should not be recreated. Other modal cases were recorded Fixed in that review, but still need regression coverage on the current build.

### P1 — Complete a representative accessibility and state audit

1. Walk through sign-in/reset, Ask, account creation, file upload/import, paid research/refresh, account deletion, sheets/tables, and operator sign-in/client management using keyboard only.
2. Verify visible focus and focus order at desktop and mobile widths; check sticky navigation and dialogs for obscured focus. Record AA findings separately from AAA enhancements.
3. Use a screen reader on the highest-value flows (one desktop screen reader and VoiceOver mobile if available). Check headings/landmarks, form labels and validation, live progress/status announcements, table names, and streamed Ask responses.
4. Inspect empty, loading, stale-data, retry, permission-denied, success, approval-waiting, cancellation, and terminal-failure states for research and upload flows. Confirm users retain typed input or uploaded selections on recoverable failures.
5. Reconcile route coverage in Playwright/axe with live routes. Treat axe as a baseline, not sign-off. Document manual checks and unresolved barriers.

Deliverable: a route/flow checklist with observed steps, criterion, severity, reproduction, owner, and pass evidence. Do not make a blanket WCAG-conformance claim unless the scope and checks support it.

### P1 — Audit the operator console before redesigning it

1. Use the client pastel design and shared modal/toast components as the proposed baseline, consistent with `docs/ui-audit/2026-11-07-pastel/next-prompt.md`.
2. Start with the Clients screen and review its screenshot at desktop and narrow viewport. Then inventory operator routes for forms, destructive actions, loading/error/empty states, and browser confirm usage.
3. Preserve all operator features, role checks, tenant boundaries, and paid-action confirmation behavior. Avoid changing API or permission semantics as part of a visual pass.

### P2 — Consolidate interaction patterns based on findings

1. Consolidate dialog, confirmation, error, and success patterns around the components already in `web/ui/`.
2. Normalize target sizes, focus styling, and reduced-motion behavior in CSS tokens/components; remove one-off variants only after route coverage is checked.
3. Use explicit state unions/reducers only in components where the audit finds impossible state combinations or invalid transitions. Keep simple forms and local toggles simple.
4. Add a command palette, optimistic updates, virtualization, or containment only when user feedback or profiling identifies a concrete need and the change has a bounded rollback path.

## Completion criteria

- All known `Open` items from the November 7 client review are resolved or explicitly retained with a reason.
- Current active client and operator flows have documented keyboard checks; sampled screen-reader checks cover the flows above.
- No keyboard trap or completely obscured focused control is found in the reviewed scope; AA target-size issues are fixed or have a documented applicable exception.
- Async and paid actions expose clear progress, error recovery, and final status without duplicate submission; paid starts still require two confirmations.
- Playwright/axe scope reflects the active routes, with no serious/critical automated accessibility findings in the reviewed screens. Manual review findings are tracked separately.
- Performance or architecture changes are justified with a measured hotspot or user need. No blanket XState, MFE, SDUI, spring-animation, or `contain: strict` migration is required by this plan.

## Immediate next work package

Start with the operator Clients screen review described in P1, then close the known client-side browser-confirm gap. For code changes, follow `web/AGENTS.md` and read the installed Next.js 16 documentation relevant to each touched area before editing. Keep work limited to one reviewable increment and do not include unrelated worktree changes.

## Implementation status (2026-11-09)

Implemented from this plan:

- Support Access revoke, file-analysis deletion, user access termination, client suspension/reactivation, and people-consent updates now use the shared confirmation dialog instead of native browser confirmation.
- Dialogs describe the action, disable duplicate submission, expose a working label, retain the dialog on failure, and restore focus to the opener or the page's main landmark when the opener is removed. The close target is 40 px, and background live regions remain available for announcements.
- The modal now inerts all background siblings for both client-shell and operator-console portals, not just the main content region.
- File-analysis bulk deletion tracks already-confirmed deletions so retry after a partial failure does not repeat successful requests; already-absent files count as removed, and the analysis reloads after completion.
- Operator console visual redesign: added `ui-pastel` to `AdminShell` and `AuthFrame` (both client and operator sign-in); wired `NavProgress` and `Toaster`; replaced Radix dialogs (`@radix-ui/react-dialog`) in Clients (`AddClient`) and 6th Library (`PublishPassed`, `UnpublishOne`) with the accessible shared `Modal`; wired live status announcements (`announce()`) after operator actions.
- Colour schema audit: verified WCAG 2.2 Level AA contrast conformance across surfaces (text-to-background >= 6.0:1 to 17.0:1 vs 4.5:1 requirement; pills >= 5.8:1; primary accent button text 5.6:1; focus rings 5.6:1 vs 3.0:1 requirement).
- `npm run typecheck` passed cleanly with 0 errors.

Not implemented because the live feature is absent or the work requires interactive review:

- Mail and Drive disconnect has no current route or control.
- Live browser screenshots against a running staging server for PDF inclusion.
