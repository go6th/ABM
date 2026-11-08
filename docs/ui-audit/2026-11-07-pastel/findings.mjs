// The audit's findings as the report lists them: what the user saw, the cause, what it does now,
// and a status (Fixed, Removed, Kept, Open). Kept means left on purpose, with the reason in "now".
export const FINDINGS = [
  {
    title: "Pop-ups, menus and buttons",
    intro: "Every pop-up is now one shared, accessible dialog; menus close the way people expect; every button shows when it is working.",
    items: [
      { saw: "Pressing Escape, the backdrop or × while a refresh was being sent closed the pop-up; its error was never seen.", cause: "The pop-up did not know a start was in flight.", now: "The pop-up cannot close while a start is being sent; a started refresh says so in a note and opens the Overview.", status: "Fixed" },
      { saw: "Escape in the model menu closed the whole Refresh pop-up and lost the typed topic.", cause: "Both listened to Escape on the page and neither checked the other.", now: "Escape closes only the open menu first; a second Escape closes the pop-up.", status: "Fixed" },
      { saw: "Model and effort could be changed while \"Are you sure?\" showed, and the cost shown did not change.", cause: "The confirm step kept its state when the form changed.", now: "Any change to the form disarms the confirm; the cost shown is always the picked option's.", status: "Fixed" },
      { saw: "Delete account had no Escape, focus stayed behind it, the page scrolled, and the … menu stayed open after \"Keep it\".", cause: "A hand-made panel without focus handling.", now: "Shared dialog: focus moves to the domain box, Tab stays inside, Escape and Keep it close it, focus returns, the menu closes.", status: "Fixed" },
      { saw: "Focus could leave the Refresh pop-up with Shift+Tab, and the page behind could be scrolled and tabbed into.", cause: "Focus started on the panel; the page behind was not made inert.", now: "Focus starts on the first control and is kept inside; the page behind is inert and does not scroll.", status: "Fixed" },
      { saw: "The account's More and … menus never closed after a choice, an outside click or Escape, and both could be open at once.", cause: "Plain <details> elements with no script.", now: "They close on a choice, a page change, an outside click and Escape; only one is open at a time.", status: "Fixed" },
      { saw: "On a More page (Archive, Coverage …) no tab looked open.", cause: "Only the eight main tabs knew the address.", now: "More shows the open page's name, underlined like a tab.", status: "Fixed" },
      { saw: "At phone width the More menu opened off the left edge.", cause: "The menu was pinned to the right edge of a left-aligned button.", now: "Pinned to the left on phones.", status: "Fixed" },
      { saw: "Rotating a tablet with the phone menu open left the page frozen with no way to close the menu.", cause: "The menu only closed on its own buttons, which hide at that width.", now: "The menu closes when the page changes or the window widens.", status: "Fixed" },
      { saw: "Switching Research tabs emptied the uploaded files and lost typed text.", cause: "The other tab was unmounted.", now: "A tab stays mounted (hidden) once opened.", status: "Fixed" },
      { saw: "\"Download files\" in the account header repeated the Files tab.", cause: "Two ways to the same place.", now: "Removed; Files is a tab.", status: "Removed" },
      { saw: "The research and refresh forms showed four model chips (Research, Research effort, Analysis, Analysis effort).", cause: "Every default exposed on every form.", now: "Two chips: the research model and its effort. The analysis model comes from Settings > Models.", status: "Removed" },
      { saw: "\"Start research\" stayed grey with no reason when the website was wrong.", cause: "Only an aria-invalid flag, no text.", now: "\"Enter the website as company.com.\" under the box, and the reason under the disabled button.", status: "Fixed" },
      { saw: "Some buttons and menu items were 24–36 px tall.", cause: "Older one-off styles.", now: "At least 36–40 px; the close button is 40 px.", status: "Fixed" },
      { saw: "A click on a link or tab showed nothing for up to 10 s on slow pages.", cause: "No loading boundary under the account and Settings frames; no click feedback.", now: "A glowing bar across the top, the clicked tab or link shows it is opening, and the tab body shows placeholders at once.", status: "Fixed" },
    ],
  },
  {
    title: "Spending money: the confirm steps",
    intro: "The owner's rule is kept: two confirmations before any paid start, and the server still refuses a start without both.",
    items: [
      { saw: "Three presses on three different screens of text: Confirm refresh, then Proceed?, then Are you sure?", cause: "Each question replaced the one before.", now: "The cost is shown up front; the first press arms the button (it glows and asks \"Are you sure?\"); the second starts.", status: "Fixed" },
      { saw: "\"Confirm refresh\" confirmed nothing; it only opened the next question.", cause: "Label written for the old flow.", now: "The button says what it does: Refresh, Research this topic, Start research, Analyze file.", status: "Fixed" },
      { saw: "\"Fix it first\" in the confirm left the form and lost what was typed.", cause: "The link opened in the same tab.", now: "Opens in a new tab.", status: "Fixed" },
      { saw: "A successful refresh closed the pop-up with no confirmation at all.", cause: "No feedback after the start.", now: "\"Refresh started. You will be notified when it finishes.\" and the Overview with its progress.", status: "Fixed" },
    ],
  },
  {
    title: "Refresh and live updates",
    intro: "Screens now update when something actually changed, stop when the tab is hidden, and never die after one failed check.",
    items: [
      { saw: "One failed background check replaced the whole page (or the whole app) with an error and stopped all live updates.", cause: "The layout threw on any /v1/me failure; failed pages unmounted the pollers.", now: "Only a signed-out session leaves the page; failed sections show Try again and keep retrying (15 s, 30 s, 60 s).", status: "Fixed" },
      { saw: "A queued or credit-waiting research reloaded the whole page every 10 s, for days.", cause: "A timer refreshed the full page.", now: "A light status read every 10 s (60 s while waiting for credit); the page redraws only when the status changes.", status: "Fixed" },
      { saw: "Reading a zip redrew the whole page every 4 s.", cause: "Every counter change counted as a change.", now: "Redraws only when a file's stage changes.", status: "Fixed" },
      { saw: "An import longer than 15 minutes froze at \"Indexing\" until reload.", cause: "The give-up window never restarted.", now: "The window restarts on every change and when the tab is shown again.", status: "Fixed" },
      { saw: "Overview counts and findings stayed frozen during a whole research.", cause: "Only the progress card was polled.", now: "The page redraws when a step finishes (at most every 30 s).", status: "Fixed" },
      { saw: "File analysis reloaded the page every 5 s forever when waiting for approval or stalled.", cause: "\"Done\" excluded those states.", now: "A small read with back-off (5 s to 30 s); stops on done, approval, failure; plain cards for each.", status: "Fixed" },
      { saw: "Everything kept polling in hidden tabs; the run page fetched its spend twice every 5 s.", cause: "No visibility check; an object in the effect's dependencies.", now: "All live reads pause while the tab is hidden and catch up when shown; the duplicate read is gone.", status: "Fixed" },
      { saw: "Requests piled up on a slow API; a missing run polled forever; sign-out did not stop polling.", cause: "Fixed-interval timers with no in-flight guard.", now: "Each read waits for the last; back-off to 30 s; 401 and 404 stop it.", status: "Fixed" },
      { saw: "One failed poll replaced the run's steps, Stop and Continue with an error.", cause: "Error took priority over loaded data.", now: "Last data stays with a small \"Reconnecting…\" line.", status: "Fixed" },
      { saw: "Viewers got an extra full reload on every account visit.", cause: "Their 403 on the uploads list counted as a failure.", now: "403 means no uploads.", status: "Fixed" },
      { saw: "File statuses froze while another file uploaded.", cause: "The status timer was reset by every progress event.", now: "One steady timer; and leaving the page during an upload asks first.", status: "Fixed" },
      { saw: "Run page: \"You can leave this page\" beside \"not found\"; a stopped run said \"Finished\"; a manual Refresh on a live page.", cause: "Texts written for the happy path.", now: "Texts match the run's real state; the manual Refresh and the duplicate counts are gone.", status: "Fixed" },
    ],
  },
  {
    title: "Errors, words and clutter",
    intro: "Errors are short plain sentences with a way to try again; internal names and repeated text are gone.",
    items: [
      { saw: "\"Internal Server Error\", \"Unprocessable Content\", \"invalid request\", a contract-mismatch line, or a blank alert in forms.", cause: "The API's status text was printed as the message.", now: "One helper shows the API's own sentence only when it wrote one; otherwise a plain line for that action.", status: "Fixed" },
      { saw: "The error page's Reload did nothing for server errors.", cause: "reset() redrew the cached output.", now: "Reload fetches again (Next's retry).", status: "Fixed" },
      { saw: "Table pages showed database table names and raw tool codes such as p_search:web_search.", cause: "Server text written for engineers.", now: "Plain scope lines; tools read \"Web search\", \"Page read\"; arguments read as words.", status: "Fixed" },
      { saw: "Three download buttons on each table page and a \"Click a column heading to sort\" hint.", cause: "Every format offered everywhere.", now: "Two: the delivered file and the table as Excel.", status: "Removed" },
      { saw: "The sign-in workspace picker showed a long internal id and a role code.", cause: "The API sends no name.", now: "\"Workspace 91ac (Analyst)\"; real names need the API to send them.", status: "Fixed" },
      { saw: "Email verification said to use \"Security settings\", which does not exist.", cause: "Stale text.", now: "Points to the sign-in page's Send a new link, or the administrator.", status: "Fixed" },
      { saw: "Deletes used the browser's own grey confirm box, and removing a paused upload deleted it without asking.", cause: "window.confirm and no check.", now: "The shared confirm dialog, a plain error if it fails, and a short note when done.", status: "Fixed" },
      { saw: "Non-admins saw Team and Support access tabs that only said \"Only your administrator…\".", cause: "Tabs not filtered by role.", now: "Shown to admins only.", status: "Fixed" },
      { saw: "Overview: \"Web: not used for answers\", two help sentences, a 0 mail-and-drive row, a manual Refresh and two links to the same run.", cause: "Text added over time.", now: "Removed; one link to the run.", status: "Removed" },
      { saw: "The Research board showed Resume twice and an empty-state card beside the start form.", cause: "Two buttons with separate request keys.", now: "One Resume in the detail panel; one quiet line when nothing runs.", status: "Removed" },
      { saw: "Settings: \"Used by…\" lines and repeated intros.", cause: "Text repeated by the controls.", now: "Trimmed.", status: "Removed" },
      { saw: "Mail and drive Disconnect and Support access Revoke still use the browser confirm box.", cause: "Not in this round.", now: "Works; to move to the shared dialog next.", status: "Open" },
      { saw: "The per-answer token line, context ring, token card and the More audit tables (Calls made, Searches run, Notes).", cause: "Owner designs and client deliverables.", now: "Kept as they are.", status: "Kept" },
    ],
  },];

export const NEXT_PROMPT = `Redesign the operator (admin) console in web/app/operator/** to match the client app's pastel 3D look.
- Reuse web/ui/depth.css: add the class "ui-pastel" to the operator shell root (app/operator/(console)/AdminShell.tsx) and the operator sign-in AuthFrame, then fix anything that looks wrong there.
- Use the shared pop-up web/ui/Modal.tsx (and ConfirmDialog) instead of the Radix dialogs, NavProgress for click feedback, and announce() from web/ui/Toaster.tsx after actions.
- Keep every operator feature and the owner's money rules (confirm twice before any paid start).
- Show me one screen first (Clients) as a screenshot before changing the rest.
- Then audit the console the same way as the client app: refresh/polling, pop-ups, buttons, error text, clutter; fix, run tsc, eslint and vitest, and add the screens to the PDF report in docs/ui-audit/2026-11-07-pastel.`;
