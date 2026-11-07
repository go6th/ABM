// Builds the client web audit + redesign report as HTML and prints it to PDF.
import { writeFileSync, readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire("/home/sakib/ABM Generator/web/package.json");
const { chromium } = require("@playwright/test");

const DIR = new URL(".", import.meta.url).pathname;
const OUT = process.argv[2];
const cur = (n) => `file://${DIR}../cur/${n}.jpg`;
const mock = (n) => `file://${DIR}png/${n}.jpg`;
const backend = existsSync(DIR + "backend.html") ? readFileSync(DIR + "backend.html", "utf8") : "<p>Backend audit pending.</p>";

let pageNo = 0;
const page = (body, cls = "") => { pageNo++; return `<section class="pg ${cls}">${body}<footer><span>Client web audit and redesign</span><span>${pageNo}</span></footer></section>`; };

const verdictTag = (v) => {
  const map = { Keep: "confirmed", Simplify: "probable", Merge: "lead", Remove: "bad", Fix: "hypothesis" };
  return `<span class="g ${map[v] ?? "plain"}">${v}</span>`;
};

/* ---------- page inventory ---------- */
const inventory = [
  ["Ask (home)", "/", "Simplify", "Model, effort, token ring and a $ line under every answer compete with the answer. \"Search: off\" looks like a toggle but opens Refresh. One-line input for long questions. The sources panel is rendered twice.", "Keep as home. One quiet composer row; answer settings in one menu; keep the context ring (your choice)."],
  ["All chats", "/chats", "Merge", "A second list of the sidebar's Recent chats. No search or paging. Delete uses the browser's own confirm box.", "A searchable history panel inside Ask."],
  ["Accounts", "/accounts", "Simplify", "\"Lanes\" column shows \"No lanes yet\" on every row. No search or filter. Refresh column repeats the Schedules page. Scrolls sideways on phones.", "Add search and status filter; auto refresh editable in the row."],
  ["Add account", "/accounts/add", "Merge", "Three numbered cards; the third is only a link to another page. Model and effort chips at the very first step.", "One \"Start research\" form on the Research page and in a dialog from Accounts."],
  ["Account", "/accounts/[domain]", "Simplify", "About 2,600 lines and 50+ controls: 17-link Deep dive rail, 4 tiles that repeat the rail, 5 download buttons (two both called \"Download all\"), Delete next to the main action, QA text such as \"this view is not to be quoted\".", "Tabbed account page: Overview, People, Jobs, Technology, News and signals, Sources, Files, Activity. One main action."],
  ["Data sheets", "/accounts/[domain]/[sheet]", "Simplify", "Three downloads in the header, two pagers, empty columns shown, raw values such as \"data_ai\" and \"tableau_analytics\", filter chips that all read 0.", "Becomes the body of each account tab. One Download menu, one pager, hide empty columns."],
  ["File analysis", "/accounts/[domain]/files", "Fix + Merge", "Live bug: \"Your files speak to undefined\", \"Analysis of 0 files\", \"The files were deleted\" while the account shows 19 ready files; text overlaps the file path. Back link says \"New research\".", "Section of the account Files tab."],
  ["Archive", "/accounts/[domain]/archive", "Simplify", "9 free-text filters, a 10-column table, \"Download JSON\", back link shows the domain instead of the name.", "Keep the archived-copy view; list lives under Sources with one search box."],
  ["Refresh", "/accounts/[domain]/refresh", "Merge", "Tokens and $ on every option, model chips, \"Stay local\" button. A full page for one choice.", "A two-step dialog on the account page (confirm twice kept)."],
  ["Document viewer", "/accounts/[domain]/documents/[id]", "Simplify", "Server makes up to 50 calls of 200 passages in a row before showing anything; the test page grew to 1.3 million pixels tall. Words like \"passages\", \"Raw pull\", \"Build file\".", "File viewer inside Files: contents list, search, paged text."],
  ["Research board", "/research", "Simplify", "\"Model tokens\" tile, \"withheld\" wording, connection status chatter, a third start-research form. Showed \"No research finished\" while two runs had finished 4 days earlier.", "Research page = start form + running + recent."],
  ["New research", "/research/new", "Merge", "Company tab is the same form as Add account. File tab shows \"Scanning\" and \"Indexing\".", "Tab of the Research start form."],
  ["Research run", "/runs/[run_id]", "Remove", "The most internal screen a client can reach: 13 steps all \"Done\" next to \"0 of 6 checklist rows reported\", 0 tokens, \"Units used\", 33-row coverage table of \"Not yet reported\", pre-delivery gate codes.", "Plain 5-step progress page under Research."],
  ["Schedules", "/schedules", "Remove", "A whole nav item for one setting per account. \"Everything stays local\".", "Auto refresh set in the Accounts list and the account Activity tab."],
  ["Mail and drive", "/mail-and-drive", "Merge", "Fine on its own; its own copy of the status pill; browser confirm on Disconnect.", "Settings, Mail and drive tab."],
  ["Model preferences", "/model-preferences", "Merge", "Reachable only from the sidebar footer; the same choices repeat as chips on Ask, Add account and Refresh.", "Settings, Models tab."],
  ["Team", "/team", "Merge", "People-data consent is hidden under Teammates. Different input style from the rest.", "Settings, Team tab."],
  ["Support access", "/support-access", "Merge", "A top-level nav slot for a rare admin task.", "Settings, Support access tab."],
  ["Sign in", "/login", "Simplify", "Workspace picker shows raw IDs; the 6th staff console link sits on the client sign-in.", "Keep the split layout; workspace names; staff link in the footer."],
  ["Forgot, reset, invite, verify", "/forgot …", "Keep", "Invite-expired copy says \"Ask 6th\"; invites now come from your own admin. Verify page has no explanation.", "Same frame as Sign in; fix the copy."],
  ["Privacy, Terms", "/privacy, /terms", "Fix", "Published with placeholders: [Effective date], [Company legal name], [Registered address], [To be completed by counsel].", "Fill before any Google or Microsoft app review."],
];

/* ---------- top changes ---------- */
const top = [
  ["Cut the account page to tabs", "17-link rail, 4 tiles, delivered-files viewer and 5 downloads become 8 tabs and one Download menu."],
  ["Remove internal words from client screens", "gate, withheld, checklist rows, units, lanes, passages, Raw pull, Stay local, Summary homework, Distill, HTTP codes."],
  ["One way to start research", "Six entry points today (Add account, New research, Research board, Refresh, Resume, Ask search) become one form and one Refresh dialog, both with the two-step confirm."],
  ["Nav from 8 items to 4", "Ask, Accounts, Research, Settings. Schedules, Mail and drive, Team, Support access and Model preferences move into Settings."],
  ["Fix the live bugs found", "File analysis shows \"undefined\" and overlapping text; run page contradicts itself; Research board misses finished runs; raw values in tables."],
  ["Hide cost and model controls behind one menu", "Keep the estimate before any paid run and the Ask context ring; move model and effort chips into a single settings menu."],
  ["One component set", "One table, one badge, one dialog (replaces 6 browser confirm boxes), one page header, one input style. Delete the second page-frame system."],
  ["Flatter look", "Radius 8/12 instead of 20/22, no drop shadows, hairline borders, one type scale (today 21 font sizes)."],
  ["Fix the document viewer load", "Page the text on the client instead of 50 server calls before first paint."],
  ["Fill the legal pages", "Required before Google and Microsoft review the Mail and drive apps."],
];

const before = [
  ["Ask", "/"], ["Chats", "/chats"], ["Accounts", "/accounts"], ["Add account", "/accounts/add"], ["Account", "/accounts/[d]"], ["17 deep-dive sheets", "/accounts/[d]/[sheet]"],
  ["File analysis", "/accounts/[d]/files"], ["Archive", "/accounts/[d]/archive"], ["Refresh", "/accounts/[d]/refresh"], ["Document", "/accounts/[d]/documents/[id]"],
  ["Research", "/research"], ["New research", "/research/new"], ["Run", "/runs/[id]"], ["Schedules", "/schedules"], ["Mail and drive", "/mail-and-drive"],
  ["Model preferences", "/model-preferences"], ["Team", "/team"], ["Support access", "/support-access"],
];

const after = [
  ["Ask", "Chat, history panel, sources"],
  ["Accounts", "List with search, filter, auto refresh; Add account dialog"],
  ["Account", "Overview · People · Jobs · Technology · News and signals · Sources · Files · Activity; Refresh dialog"],
  ["Research", "Start (company or file) · Running · Recent · Progress page"],
  ["Settings", "Models · Team · Mail and drive · Support access · Usage"],
];

const screensOrder = [
  ["signin", "Sign in", "Same split layout, calmer. The right side shows the product's real promise: a sentence with citations and the four grades. Staff sign-in moves to the footer. Workspace picker (not shown) lists names, not IDs."],
  ["ask", "Ask", "The answer is the page. One composer row: account, web search, model, and your context ring. Sources sit in one column on the right with where each came from. The $ line under each answer is gone from view."],
  ["accounts", "Accounts", "One table, one status per account in plain words, the platforms we saw instead of \"No lanes yet\", auto refresh edited in the row. Filter by status. Import a file sits beside Add account."],
  ["account", "Account, Overview", "Tabs replace the 17-link rail and the 4 tiles. One main action (Refresh); Schedule and Delete move into the … menu. Coverage becomes one honest line: 18 of 33 areas, with the gaps listed under Sources."],
  ["jobs", "Account, Jobs tab", "Every data sheet uses the same table: readable platform names, a real pager, filters that only show when they have rows, one Download button."],
  ["files", "Account, Files tab", "Research files, your uploads and earlier versions in one place. One \"Download everything\". The privacy rule is stated once instead of on every row."],
  ["refresh", "Refresh dialog", "Refresh opens over the account instead of a separate page. Step 1 picks the scope with time and price; step 2 confirms the cost (your confirm-twice rule stays)."],
  ["research", "Research", "Start a research and watch it in one place. Model choice is one line with a link to Settings. Recent runs show plain results: Ready, Ready with gaps, Stopped."],
  ["run", "Research progress", "13 internal steps become 5 plain ones. Live counts of what was found, tokens against the estimate with the 3x pause rule, and a clear Stop."],
  ["settings", "Settings, Models", "Every workspace setting behind one nav item, as tabs. Models are a simple radio list per job with the effort switch beside it."],
];

const css = `
@import url("https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap");
@page { size: 297mm 210mm; margin: 0; }
:root { --ink:#1B2140; --ink-2:#3A4363; --muted:#646B86; --line:#E4E7F2; --primary:#3F5AE0; --wash:#E9EDFF; --side:#EEF1FE; --canvas:#F7F8FD;
  --mint:#E3F5EE; --mint-ink:#17694A; --butter:#FFF4D6; --butter-ink:#7A5A00; --rose:#FFE8EE; --rose-ink:#A3294B; --sky:#E7EEFF; --sky-ink:#2747C7; --lav:#F0EAFF; --lav-ink:#5B3FC4; }
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:"Plus Jakarta Sans", system-ui, sans-serif; color:var(--ink); font-size:10.5pt; line-height:1.5; -webkit-print-color-adjust:exact; print-color-adjust:exact; font-feature-settings:"tnum" 1; }
.pg { width:297mm; height:210mm; padding:16mm 18mm 14mm; position:relative; page-break-after:always; overflow:hidden; background:#fff; }
.pg footer { position:absolute; left:18mm; right:18mm; bottom:8mm; display:flex; justify-content:space-between; font-size:8pt; color:var(--muted); border-top:1px solid var(--line); padding-top:3mm; }
h1 { font-size:30pt; line-height:1.1; letter-spacing:-0.02em; font-weight:700; }
h2 { font-size:19pt; line-height:1.2; letter-spacing:-0.015em; font-weight:700; margin-bottom:2mm; }
h3 { font-size:11.5pt; font-weight:700; margin-bottom:1.5mm; }
.lede { font-size:11.5pt; color:var(--ink-2); max-width:200mm; }
.muted { color:var(--muted); }
.small { font-size:8.8pt; color:var(--muted); }
.g { display:inline-flex; align-items:center; gap:5px; font-size:8pt; font-weight:600; border-radius:999px; padding:1px 8px 1px 6px; white-space:nowrap; }
.g::before { content:""; width:6px; height:6px; border-radius:50%; background:currentColor; }
.g.confirmed { background:var(--mint); color:var(--mint-ink); } .g.probable { background:var(--sky); color:var(--sky-ink); }
.g.hypothesis { background:var(--butter); color:var(--butter-ink); } .g.lead { background:var(--lav); color:var(--lav-ink); }
.g.bad { background:var(--rose); color:var(--rose-ink); } .g.plain { background:#F0F2F8; color:#4A5270; }
table { width:100%; border-collapse:collapse; font-size:8.6pt; }
th { text-align:left; font-weight:600; color:var(--muted); padding:2mm 2.5mm; border-bottom:1px solid var(--line); background:#FAFBFE; font-size:8pt; }
td { padding:2mm 2.5mm; border-bottom:1px solid #EEF0F6; vertical-align:top; }
code { font-size:8pt; color:var(--ink-2); background:#F2F4FA; padding:0 3px; border-radius:3px; font-family:ui-monospace, monospace; }
.shot { border:1px solid var(--line); border-radius:6px; overflow:hidden; background:#fff; }
.shot img { display:block; width:100%; }
.cols { display:grid; gap:9mm; }
.num-row { display:flex; gap:10mm; margin-top:9mm; }
.num-row div b { display:block; font-size:24pt; font-weight:700; letter-spacing:-0.02em; color:var(--primary); line-height:1.1; }
.num-row div span { font-size:9pt; color:var(--ink-2); }
ol.top { list-style:none; counter-reset:t; display:grid; grid-template-columns:1fr 1fr; gap:3.5mm 10mm; }
ol.top li { counter-increment:t; display:grid; grid-template-columns:8mm 1fr; }
ol.top li::before { content:counter(t); font-weight:700; color:var(--primary); font-size:11pt; }
ol.top b { display:block; font-size:10pt; }
ol.top span { font-size:8.8pt; color:var(--ink-2); }
.cover { background:var(--side); display:flex; flex-direction:column; justify-content:space-between; padding:20mm 22mm; }
.cover .mark { width:12mm; height:12mm; border-radius:3mm; background:var(--primary); color:#fff; display:grid; place-items:center; font-weight:700; font-size:14pt; }
.cover h1 { font-size:40pt; max-width:220mm; margin-top:4mm; }
.cover .meta { display:grid; grid-template-columns:repeat(4, auto); gap:12mm; justify-content:start; font-size:9.5pt; }
.cover .meta b { display:block; font-size:8.5pt; color:var(--muted); font-weight:600; }
.issue { border-left:3px solid var(--primary); padding:0 0 0 4mm; }
.issue.bad { border-color:var(--rose-ink); } .issue.warn { border-color:#C79A1E; }
.issue p { font-size:9pt; color:var(--ink-2); }
.today { display:grid; grid-template-columns:1fr 1fr; gap:0 2.5mm; }
.map { display:grid; grid-template-columns:1.3fr 10mm 1fr; gap:4mm; align-items:start; }
.chip { display:flex; justify-content:space-between; gap:4mm; border:1px solid var(--line); border-radius:5px; padding:1.6mm 3mm; font-size:8.6pt; margin-bottom:1.6mm; }
.chip code { background:none; padding:0; color:var(--muted); }
.chip.new { border-color:#C9D2FA; background:#F7F8FF; display:block; padding:2.6mm 3.5mm; margin-bottom:2.6mm; }
.chip.new b { display:block; font-size:10pt; }
.swatch { height:16mm; border-radius:4px; border:1px solid var(--line); }
.mock-page { padding:12mm 16mm 14mm; }
.mock-page .shot { box-shadow:none; }
.caption { display:grid; grid-template-columns:62mm 1fr; gap:8mm; margin-bottom:5mm; align-items:start; }
.caption p { font-size:9.6pt; color:var(--ink-2); }
.pair { display:grid; grid-template-columns:1fr 1fr; gap:7mm; }
.pair .shot { height:63mm; } .pair .shot img { height:100%; object-fit:cover; object-position:top left; }
.pair .lbl { font-size:8.5pt; font-weight:600; margin-bottom:1.5mm; color:var(--muted); }
.pair .lbl.new { color:var(--primary); }
table.tight td, table.tight th { padding:1.3mm 2mm; }
.be h3 { margin-top:4mm; }
.be ul { padding-left:4.5mm; font-size:8.8pt; color:var(--ink-2); }
.be li { margin-bottom:1mm; }
.approve { border:1px solid var(--line); border-radius:6px; padding:5mm 6mm; }
.box { display:inline-block; width:4mm; height:4mm; border:1.5px solid var(--ink-2); border-radius:1mm; vertical-align:-0.6mm; margin-right:2.5mm; }
`;

let html = "";

/* 1 cover */
html += page(`<div class="mark">6</div>
<div><div class="small" style="font-size:11pt;color:var(--primary);font-weight:600">Account Intelligence, client web app</div>
<h1>Client web audit and redesign proposal</h1>
<p class="lede" style="margin-top:6mm">Every client page was opened, read in code and checked against the API behind it. This document shows what is wrong today, what to merge or remove, and how each screen should look. Nothing in the code has been changed. Approve it, or mark what to change.</p></div>
<div class="meta"><div><b>Date</b>7 November 2026</div><div><b>Scope</b>Client web only (operator console excluded)</div><div><b>Pages audited</b>23 routes, 17 data sheets</div><div><b>Branch</b>feat/final-product</div></div>`, "cover");

/* 2 summary */
html += page(`<h2>Summary</h2>
<p class="lede">The app works, but it shows clients too much of how it works. The account page carries 50+ controls, research can be started from six places, and internal words from the pipeline reach client screens. The fix is mostly removal: fewer pages, one main action per screen, plain words, and a flatter look in the white and pastel palette you already chose.</p>
<div class="num-row">
 <div><b>18 → 5</b><span>client areas after merging (plus sign-in and legal)</span></div>
 <div><b>8 → 4</b><span>sidebar nav items</span></div>
 <div><b>50+ → ~15</b><span>controls on the account page</span></div>
 <div><b>6 → 1</b><span>ways to start a paid research</span></div>
 <div><b>21 → 6</b><span>font sizes in use</span></div>
</div>
<h3 style="margin-top:10mm;margin-bottom:4mm">The ten changes that matter most</h3>
<ol class="top">${top.map((t) => `<li><div><b>${t[0]}</b><span>${t[1]}</span></div></li>`).join("")}</ol>`);

/* 3 themes */
html += page(`<h2>What is wrong today</h2>
<p class="lede" style="margin-bottom:7mm">Six problems repeat across the client app. Each one is backed by what we saw on screen and in the code.</p>
<div class="cols" style="grid-template-columns:1fr 1fr 1fr;row-gap:8mm">
 <div class="issue bad"><h3>Internal words reach clients</h3><p>Run steps called "Summary homework", "Distill" and "Pre delivery gate"; "checklist rows reported"; "Units used"; "withheld"; "Lanes"; "passages", "Indexing", "Raw pull"; "Stay local"; HTTP status codes in the Delete account error; QA banners that say a view is "not to be quoted".</p></div>
 <div class="issue bad"><h3>The account page is overloaded</h3><p>About 2,600 lines of code and 50+ controls. A 17-link Deep dive rail, 4 tiles that repeat 4 of those links, a progress card, summary, findings, a composer that leaves the page, a full delivered-files viewer and two file cards. Five download buttons, two of them both called "Download all" doing different things.</p></div>
 <div class="issue"><h3>The same job in many places</h3><p>Start research: Add account, New research, the Research board, Refresh, Resume and Ask's search toggle. Watch a run: the account card, the Research board and the run page. Uploaded files: four different views. Schedule: a page, a button and a column.</p></div>
 <div class="issue warn"><h3>Bugs visible on screen</h3><p>File analysis says "Your files speak to undefined" and "Analysis of 0 files" while the account lists 19 ready files; finding text overlaps the file path. The run page says every step is Done next to "0 of 6 rows reported" and 0 tokens. Jobs show raw values like "data_ai". People filters all read 0.</p></div>
 <div class="issue warn"><h3>Two design systems</h3><p>Two page frames, four table components, five badge styles, three input styles, six browser confirm boxes plus a custom modal, 21 font sizes, a second off-palette colour set for the token card, and the logo still in the old colours.</p></div>
 <div class="issue warn"><h3>Not ready for outside eyes</h3><p>Privacy and Terms are live with "[Effective date]" and "[Company legal name]". Google and Microsoft will read the privacy page when they review the Mail and drive apps. The sign-in workspace picker shows raw IDs.</p></div>
</div>`);

/* 4 bugs evidence */
html += page(`<h2>Evidence from the running app</h2>
<p class="lede" style="margin-bottom:5mm">Screenshots taken on 7 November from the local build of this branch, signed in as the test workspace.</p>
<div class="pair">
 <div><div class="lbl">File analysis: "undefined", "0 files", overlapping text</div><div class="shot"><img src="${cur("account-files")}"></div></div>
 <div><div class="lbl">Account page: 17-link rail, "Research finished, 1 of 33 done", 31 unknown</div><div class="shot"><img src="${cur("account")}"></div></div>
</div>
<div class="pair" style="margin-top:5mm">
 <div><div class="lbl">Jobs sheet: raw values, three downloads, a filter at 0</div><div class="shot"><img src="${cur("account-sheet-2")}"></div></div>
 <div><div class="lbl">Research board: "No research has finished" with two runs 4 days old</div><div class="shot"><img src="${cur("research")}"></div></div>
</div>`);

/* 5 run page */
html += page(`<h2>The run page shows the pipeline, not the result</h2>
<div class="cols" style="grid-template-columns:120mm 1fr;margin-top:5mm">
 <div class="shot" style="height:150mm"><img src="${cur("run-full")}"></div>
 <div>
  <p class="lede">This page is reachable by every client. It lists 13 internal steps, all marked Done, beside "0 of 6 checklist rows reported". The token card says 0 tokens and $0.00 for a finished run. Below it, a 33-row table says "Not yet reported" and "No proof recorded" on almost every line, under a green "Completed".</p>
  <p class="lede" style="margin-top:4mm">A client reading this cannot tell whether the research worked. The proposal replaces it with five plain steps, live counts of what was found, and one sentence on what could not be found and why. The pre-delivery gate, defect codes and the coverage table stay on the operator console.</p>
  <h3 style="margin-top:7mm">Also on this page</h3>
  <ul style="padding-left:4.5mm;font-size:9.4pt;color:var(--ink-2)"><li>"Units used so far" with a Refresh button in the middle of the page</li><li>A different page frame from every other screen (<code>.page</code> vs <code>.pg</code>)</li><li>Client-only render, so it is blank until all calls return</li></ul>
 </div>
</div>`);

/* 6-7 inventory */
const invRows = (rows) => rows.map((r) => `<tr><td style="width:32mm"><b>${r[0]}</b><div class="small"><code>${r[1]}</code></div></td><td style="width:20mm">${r[2].split(" + ").map(verdictTag).join(" ")}</td><td>${r[3]}</td><td style="width:62mm">${r[4]}</td></tr>`).join("");
const invHead = `<table><thead><tr><th>Page</th><th>Verdict</th><th>What is wrong</th><th>Proposal</th></tr></thead><tbody>`;
html += page(`<h2>Every client page, one by one</h2>
<p class="small" style="margin-bottom:3mm">Keep = leave as is. Simplify = same page, less on it. Merge = moves into another screen. Remove = deleted, old link redirects. Fix = broken today.</p>
${invHead}${invRows(inventory.slice(0, 7))}</tbody></table>`);
html += page(`<h2>Every client page, continued</h2>${invHead}${invRows(inventory.slice(7, 14))}</tbody></table>`);
html += page(`<h2>Every client page, continued</h2>${invHead}${invRows(inventory.slice(14))}</tbody></table>`);

/* 8 sitemap */
html += page(`<h2>From 18 client pages to 5 areas</h2>
<p class="lede" style="margin-bottom:6mm">Old addresses keep working: each one redirects to its new home, so links in emails and bookmarks do not break.</p>
<div class="map">
 <div><h3>Today</h3><div class="today">${before.map((b) => `<div class="chip"><span>${b[0]}</span><code>${b[1]}</code></div>`).join("")}</div></div>
 <div style="display:grid;place-items:center;height:100%;font-size:22pt;color:var(--primary)">→</div>
 <div><h3>Proposed</h3>${after.map((a) => `<div class="chip new"><b>${a[0]}</b><span class="small">${a[1]}</span></div>`).join("")}
  <h3 style="margin-top:6mm">Sidebar</h3><p style="font-size:9.4pt;color:var(--ink-2)">Ask, Accounts, Research, Settings. Below: recent chats and All chats. At the bottom: you, and how many researches are left this month. The big "New chat" button goes; Ask is the home page.</p>
  <h3 style="margin-top:5mm">Unchanged</h3><p style="font-size:9.4pt;color:var(--ink-2)">Sign in, forgot password, reset, invite, verify email, privacy, terms. The archived-copy view of a page stays as a deep link from Sources.</p>
 </div>
</div>`);

/* 9 design system */
const sw = [["Page", "#F7F8FD"], ["Surface", "#FFFFFF"], ["Sidebar", "#EEF1FE"], ["Ink", "#1B2140"], ["Primary", "#3F5AE0"], ["Lavender", "#F0EAFF"]];
html += page(`<h2>Design direction</h2>
<p class="lede" style="margin-bottom:6mm">Your palette from 23 October stays: white, periwinkle blue and lavender, with mint, butter and rose only for status. What changes is the weight. Today every block is a rounded card (20px corners) with a soft shadow. The proposal is flat and quieter, closer to a well-made report than a dashboard.</p>
<div class="cols" style="grid-template-columns:1fr 1fr">
 <div>
  <h3>Colour</h3>
  <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:3mm">${sw.map((s) => `<div><div class="swatch" style="background:${s[1]}"></div><div class="small" style="margin-top:1mm"><b style="color:var(--ink)">${s[0]}</b><br>${s[1]}</div></div>`).join("")}</div>
  <h3 style="margin-top:7mm">Grades are the signature</h3>
  <p style="font-size:9.4pt;color:var(--ink-2)">The one thing only this product has is the grade on every fact. It gets one consistent chip everywhere, in tables, findings and answers, and the same four colours mean the same thing on every screen.</p>
  <div style="display:flex;gap:2.5mm;margin-top:3mm"><span class="g confirmed">Confirmed</span><span class="g probable">Probable</span><span class="g hypothesis">Hypothesis</span><span class="g lead">Lead</span></div>
  <h3 style="margin-top:7mm">Status words, one set for the whole app</h3>
  <div style="display:flex;gap:2.5mm;flex-wrap:wrap"><span class="g probable">Running</span><span class="g confirmed">Ready</span><span class="g hypothesis">Ready with gaps</span><span class="g hypothesis">Waiting for you</span><span class="g bad">Stopped</span><span class="g plain">Not researched</span></div>
 </div>
 <div>
  <h3>Type</h3>
  <p style="font-size:9.4pt;color:var(--ink-2)">One family, Plus Jakarta Sans (as on the October canvas), with tabular numbers. Six sizes: 28 page title, 18 section, 15 reading text, 14 interface, 13 secondary, 12 small. Weights 400, 500, 600, 700.</p>
  <div style="margin-top:3mm;border-top:1px solid var(--line);padding-top:3mm"><div style="font-size:20pt;font-weight:700;letter-spacing:-0.02em">Synthetic Holdings</div><div style="font-size:12pt;font-weight:700">Key findings</div><div style="font-size:10.5pt">Synthetic Holdings runs Salesforce as its CRM and SAP for finance.</div><div class="small">Researched 4 days ago from public sources</div></div>
  <h3 style="margin-top:7mm">Rules</h3>
  <ul style="padding-left:4.5mm;font-size:9.2pt;color:var(--ink-2)">
   <li>Two corner sizes: 8px for controls, 12px for panels. No drop shadows.</li>
   <li>One main (blue) button per screen. Delete lives in a … menu, never beside the main action.</li>
   <li>One table component for every list and sheet, with a real pager.</li>
   <li>One dialog for every confirmation; no browser confirm boxes.</li>
   <li>Plain words only: what the user will see, not how the system works.</li>
   <li>No inline styles in the app (your CSP), so all of this lives in tokens and classes.</li>
  </ul>
 </div>
</div>`);

/* 10.. screens */
for (const [name, title, text] of screensOrder) {
  html += page(`<div class="caption"><h2>${title}</h2><p>${text}</p></div><div class="shot"><img src="${mock(name)}"></div>`, "mock-page");
}

/* before / after pair */
html += page(`<h2>Before and after, side by side</h2>
<div class="pair" style="margin-top:4mm">
 <div><div class="lbl">Today: Account</div><div class="shot"><img src="${cur("account")}"></div></div>
 <div><div class="lbl new">Proposed: Account</div><div class="shot"><img src="${mock("account")}"></div></div>
</div>
<div class="pair" style="margin-top:5mm">
 <div><div class="lbl">Today: Research run</div><div class="shot"><img src="${cur("run")}"></div></div>
 <div><div class="lbl new">Proposed: Research progress</div><div class="shot"><img src="${mock("run")}"></div></div>
</div>`);

/* backend */
html += backend.split("<!--PAGE-->").map((b) => page(`<div class="be">${b}</div>`)).join("");

/* roadmap */
html += page(`<h2>How we would build it</h2>
<p class="lede" style="margin-bottom:6mm">In four steps, each one shippable on its own. Following your rule from October, step 1 changes one screen first and you see a screenshot before the rest is restyled.</p>
<table><thead><tr><th style="width:38mm">Step</th><th>What changes</th><th style="width:52mm">You see</th></tr></thead><tbody>
<tr><td><b>1. Fixes and words</b><div class="small">no layout change</div></td><td>Fix File analysis ("undefined", overlap, 0 files), the Research board's missing finished runs, raw table values, empty filter chips and columns, the Files nav highlight. Replace internal words with plain ones in one message file. Fill legal placeholders (text from counsel). Workspace names in the sign-in picker.</td><td>Same screens, correct and readable.</td></tr>
<tr><td><b>2. Components and tokens</b></td><td>One table, badge, dialog, page header and input. Type scale and flat surfaces in <code>ui/tokens.css</code>. Restyle the Account page first and send a screenshot for your approval.</td><td>One screen in the new look, then the rest.</td></tr>
<tr><td><b>3. Structure</b></td><td>Account page tabs; Refresh as a dialog; Settings with tabs; nav to 4 items; Research page with the single start form; run page replaced by plain progress; redirects from every old address.</td><td>The new 5-area app.</td></tr>
<tr><td><b>4. Backend support</b></td><td>The API changes listed in the backend section: one client status vocabulary, one account summary call, paged documents, internal fields kept off client responses.</td><td>Faster pages, fewer loading flashes.</td></tr>
</tbody></table>
<p class="small" style="margin-top:5mm">Rules kept throughout: research never starts without the two-step confirm; nothing is committed until you say "commit"; no deploy without your go-ahead; the operator console is not touched.</p>`);

/* approval */
html += page(`<h2>Your decision</h2>
<p class="lede" style="margin-bottom:6mm">Tick what you approve and mark anything you want changed. A few points need your call because they touch rules you set earlier.</p>
<div class="cols" style="grid-template-columns:1fr 1fr">
 <div class="approve">
  <h3>Approve</h3>
  <p style="margin:3mm 0"><span class="box"></span>The 5-area structure and 4-item sidebar</p>
  <p style="margin:3mm 0"><span class="box"></span>The tabbed account page</p>
  <p style="margin:3mm 0"><span class="box"></span>Flat look: 8/12 corners, no shadows, one type family</p>
  <p style="margin:3mm 0"><span class="box"></span>Removing the run page, Schedules, New research and All chats as separate pages (with redirects)</p>
  <p style="margin:3mm 0"><span class="box"></span>Step 1 fixes can start straight away</p>
 </div>
 <div class="approve">
  <h3>Your call</h3>
  <p style="margin:3mm 0;font-size:9.6pt"><b>Model and effort chips.</b> Today on Ask, Add account and Refresh. Proposal: one menu in Ask, a single line on the research form, full choice in Settings.</p>
  <p style="margin:3mm 0;font-size:9.6pt"><b>Cost under each answer.</b> The "12K in, 3K out, $0.04" line. Proposal: hide it from the answer and show totals in Settings, Usage. The context ring stays, as you asked.</p>
  <p style="margin:3mm 0;font-size:9.6pt"><b>Run token card.</b> Your tokens.html design stays on the progress page; the "Units used" block goes.</p>
  <p style="margin:3mm 0;font-size:9.6pt"><b>Font.</b> The app uses Hanken Grotesk; the October canvas used Plus Jakarta Sans. These mockups use Plus Jakarta Sans.</p>
 </div>
</div>`);

const doc = `<!doctype html><html><head><meta charset="utf-8"><title>Client web audit and redesign</title><style>${css}</style></head><body>${html}</body></html>`;
writeFileSync(DIR + "report.html", doc);
const browser = await chromium.launch({ headless: true });
const p = await (await browser.newContext()).newPage();
await p.goto(`file://${DIR}report.html`, { waitUntil: "networkidle" });
await p.evaluate(() => document.fonts.ready);
await p.pdf({ path: OUT, width: "297mm", height: "210mm", printBackground: true });
await browser.close();
console.log("pages", pageNo, "->", OUT);
