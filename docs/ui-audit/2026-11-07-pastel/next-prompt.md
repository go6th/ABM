# Prompt for the next round (admin console)

Redesign the operator (admin) console in web/app/operator/** to match the client app's pastel 3D look.
- Reuse web/ui/depth.css: add the class "ui-pastel" to the operator shell root (app/operator/(console)/AdminShell.tsx) and the operator sign-in AuthFrame, then fix anything that looks wrong there.
- Use the shared pop-up web/ui/Modal.tsx (and ConfirmDialog) instead of the Radix dialogs, NavProgress for click feedback, and announce() from web/ui/Toaster.tsx after actions.
- Keep every operator feature and the owner's money rules (confirm twice before any paid start).
- Show me one screen first (Clients) as a screenshot before changing the rest.
- Then audit the console the same way as the client app: refresh/polling, pop-ups, buttons, error text, clutter; fix, run tsc, eslint and vitest, and add the screens to the PDF report in docs/ui-audit/2026-11-07-pastel.
