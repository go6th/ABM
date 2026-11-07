// Builds the redesign mockup screens as HTML and screenshots each one.
import { writeFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire("/home/sakib/ABM Generator/web/package.json");
const { chromium } = require("@playwright/test");

const DIR = new URL(".", import.meta.url).pathname;
mkdirSync(DIR + "screens", { recursive: true });
mkdirSync(DIR + "png", { recursive: true });

const ic = {
  ask: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>',
  acc: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M9 7h6M9 11h6M9 15h3"/></svg>',
  res: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.3-4.3"/></svg>',
  set: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
  plus: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  file: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
  dl: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
  up: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M6 11l6-6 6 6"/></svg>',
  ref: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 0 0-14.9-3M4 13a8 8 0 0 0 14.9 3"/><path d="M4 4v4h4M20 20v-4h-4"/></svg>',
  more: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>',
  check: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"/></svg>',
  lock: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
  mail: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/></svg>',
  folder: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
};

const side = (on, recentOn = -1) => `
<aside class="side">
  <div class="brand"><div class="mark">6</div><div><b>Account Intelligence</b><small>by 6th</small></div></div>
  <div class="ws">Synthetic Pilot A <span>▾</span></div>
  <nav class="nav">
    <a class="${on === "ask" ? "on" : ""}"><i>${ic.ask}</i>Ask</a>
    <a class="${on === "acc" ? "on" : ""}"><i>${ic.acc}</i>Accounts</a>
    <a class="${on === "res" ? "on" : ""}"><i>${ic.res}</i>Research<em>1 running</em></a>
    <a class="${on === "set" ? "on" : ""}"><i>${ic.set}</i>Settings</a>
  </nav>
  <h6>Recent chats</h6>
  <div class="recent">
    ${["What do we know about Synthetic Holdings?", "Who owns the Salesforce platform?", "New postings this month", "Compare ERP signals across accounts", "Draft first-call questions"].map((t, i) => `<a class="${i === recentOn ? "on" : ""}">${t}</a>`).join("")}
    <a class="more-chats" href="#">All chats</a>
  </div>
  <div class="grow"></div>
  <div class="me"><div class="avatar">TU</div><div><b>Test User</b><small>2 of 2 researches left</small></div></div>
</aside>`;

const shell = (on, crumb, body, opts = {}) => `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="../mock.css"><style>${opts.css ?? ""}</style></head><body>
<div class="app">${side(on, opts.recentOn)}<main class="main">
  <header class="top"><div class="crumb">${crumb}</div><div class="grow"></div>
    <div class="search">${ic.res} Search accounts, people, files <kbd>Ctrl K</kbd></div>
    ${opts.topAction ?? ""}
  </header>
  <div class="page">${body}</div>
</main></div>${opts.overlay ?? ""}</body></html>`;

const screens = {};

/* ---------- 1. Sign in ---------- */
screens.signin = `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="../mock.css"><style>
.auth { display:grid; grid-template-columns: 1fr 1fr; height:900px; }
.auth-l { background:#fff; display:flex; flex-direction:column; padding:40px 72px; }
.auth-form { width:380px; margin:auto 0 auto 64px; }
.auth-form h1 { font-size:30px; margin-bottom:8px; }
.auth-form .stack > * + * { margin-top:18px; }
.auth-r { background:var(--side); display:flex; flex-direction:column; justify-content:center; padding:0 88px; }
.grade-demo { margin-top:36px; background:#fff; border:1px solid #DDE3F5; border-radius:12px; padding:22px 24px; width:470px; }
.grade-demo p { font-size:15px; line-height:1.55; }
.legend { display:flex; gap:8px; margin-top:16px; flex-wrap:wrap; }
.auth-r h2 { font-size:30px; line-height:1.2; letter-spacing:-0.02em; max-width:480px; }
.auth-r .sub { max-width:460px; margin-top:12px; font-size:15px; }
.or { display:flex; align-items:center; gap:12px; color:var(--faint); font-size:12.5px; }
.or::before, .or::after { content:""; flex:1; height:1px; background:var(--line); }
.foot { color:var(--muted); font-size:12.5px; display:flex; gap:16px; }
</style></head><body><div class="auth">
<div class="auth-l">
  <div class="brand"><div class="mark">6</div><div><b>Account Intelligence</b><small>by 6th</small></div></div>
  <div class="auth-form">
    <h1>Sign in</h1>
    <p class="sub">Use the work email your team invited.</p>
    <div class="stack" style="margin-top:28px">
      <div class="field"><label>Work email</label><div class="input focus">rachel.k@company.com</div></div>
      <div class="field"><div class="between"><label>Password</label><a class="small" style="color:var(--primary)">Forgot password?</a></div><div class="input">••••••••••••</div></div>
      <label class="row small" style="color:var(--ink-2)"><span style="width:16px;height:16px;border-radius:4px;background:var(--primary);display:inline-grid;place-items:center;color:#fff">${ic.check}</span>Keep me signed in on this device</label>
      <a class="btn primary" style="width:100%;justify-content:center;height:42px">Sign in</a>
      <div class="or">or</div>
      <a class="btn" style="width:100%;justify-content:center;height:42px">${ic.lock} Continue with single sign-on</a>
    </div>
    <p class="small" style="margin-top:28px">No access yet? Ask your 6th contact for an invite.</p>
  </div>
  <div class="foot"><span>Privacy</span><span>Terms</span><span style="margin-left:auto">6th staff sign in</span></div>
</div>
<div class="auth-r">
  <h2>Every account brief, with the proof behind each line.</h2>
  <p class="sub">6th researches your target accounts and grades each fact by how well it is sourced, so you know what is safe to say on a call.</p>
  <div class="grade-demo">
    <p>Synthetic Holdings named a new CIO in September <span class="cite">3</span> and is hiring four data platform roles <span class="cite">1</span>.</p>
    <div class="legend"><span class="g confirmed">Confirmed</span><span class="g probable">Probable</span><span class="g hypothesis">Hypothesis</span><span class="g lead">Lead</span></div>
  </div>
</div></div></body></html>`;

/* ---------- 2. Ask ---------- */
screens.ask = shell("ask", "<b>Ask</b>", `
<div style="display:grid;grid-template-columns:1fr 300px;gap:32px;height:100%">
 <div style="display:flex;flex-direction:column;height:100%;max-width:760px">
  <div style="flex:1;overflow:hidden">
    <div style="display:flex;justify-content:flex-end;margin-bottom:22px"><div style="background:var(--primary-wash);border-radius:12px 12px 4px 12px;padding:10px 14px;max-width:520px;font-size:14.5px">What do we know about Synthetic Holdings before Thursday's call?</div></div>
    <p style="font-size:15px;line-height:1.65">Synthetic Holdings is consolidating two ERPs and has hired a new CIO with an AI brief. Hiring is concentrated in data platform and Salesforce roles.</p>
    <div class="panel" style="margin:16px 0;overflow:hidden">
      <table class="t"><thead><tr><th style="width:120px">Area</th><th>What we know</th><th style="width:120px">Grade</th></tr></thead><tbody>
      <tr><td>Hiring</td><td>4 open roles in data and platform engineering <span class="cite">1</span></td><td><span class="g confirmed">Confirmed</span></td></tr>
      <tr><td>Technology</td><td>Salesforce and Workday named in job postings <span class="cite">2</span></td><td><span class="g probable">Probable</span></td></tr>
      <tr><td>Leadership</td><td>New CIO announced in September <span class="cite">3</span></td><td><span class="g confirmed">Confirmed</span></td></tr>
      <tr><td>Budget</td><td>CRM harmonisation likely funded in the next fiscal year <span class="cite">4</span></td><td><span class="g hypothesis">Hypothesis</span></td></tr>
      </tbody></table>
    </div>
    <h3 style="margin:18px 0 8px">Questions for the first call</h3>
    <ol style="padding-left:20px;font-size:14.5px;line-height:1.8"><li>Which of the four data roles are still open after the Q3 hiring push?</li><li>How is the ERP consolidation sequenced this year?</li></ol>
    <div class="row" style="margin-top:14px;gap:8px"><span class="btn sm">Who owns the Salesforce platform?</span><span class="btn sm">What changed in the last 30 days?</span></div>
  </div>
  <div class="panel" style="padding:14px 14px 10px;margin-top:16px;border-color:var(--line-strong)">
    <div style="color:var(--faint);font-size:14.5px;padding:2px 4px 14px">Ask a follow-up about Synthetic Holdings</div>
    <div class="between"><div class="row" style="gap:6px"><span class="icon-btn" style="width:30px;height:30px">${ic.plus}</span><span class="select">Synthetic Holdings</span><span class="select">Web search off</span><span class="select">Claude Opus 5.5</span></div>
      <div class="row" style="gap:10px"><span title="Context used" style="width:22px;height:22px;border-radius:50%;background:conic-gradient(var(--primary) 0 18%, #E6E9F4 18% 100%);display:grid;place-items:center"><span style="width:14px;height:14px;border-radius:50%;background:#fff"></span></span><span class="btn primary sm" style="width:34px;justify-content:center;padding:0">${ic.up}</span></div></div>
  </div>
  <p class="small" style="margin-top:8px">38 of 40 questions left this month</p>
 </div>
 <aside style="border-left:1px solid var(--line);padding-left:24px">
   <h3 style="margin-bottom:10px">Sources for this answer</h3>
   ${[["Careers page", "11 Oct", "Public web"], ["Job postings, technology named", "9 Oct", "Public web"], ["CIO appointment, press release", "2 Sep", "Public web"], ["Account summary, row 3", "3 Nov", "Your uploaded file"]].map((s, i) => `<div class="row" style="align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--line)"><span class="cite">${i + 1}</span><div><div style="font-size:13.5px;font-weight:500">${s[0]}</div><div class="small">${s[2]}, ${s[1]}</div></div></div>`).join("")}
   <h3 style="margin:22px 0 10px">Used for this answer</h3>
   <div class="small" style="line-height:2"><div class="between"><span>Account workspace</span><span style="color:var(--mint-ink);font-weight:600">Used</span></div><div class="between"><span>Web search</span><span>Off</span></div><div class="between"><span>Mail and drive</span><span>Not connected</span></div></div>
   <a class="btn" style="margin-top:20px;width:100%;justify-content:center">Open Synthetic Holdings</a>
 </aside>
</div>`, { recentOn: 0 });

/* ---------- 3. Accounts ---------- */
const accRows = [
  ["S", "Synthetic Holdings", "holdings-demo.example", ["confirmed", "2 new signals"], "Researched 4 days ago", "Monthly", "Salesforce, SAP"],
  ["Q", "QuidelOrtho", "quidelortho.com", ["plain", "No change"], "Researched 3 weeks ago", "Off", "Salesforce, Workday"],
  ["S", "Synthetic Signals", "holdings-signals.example", ["probable", "Research running"], "Started 12 min ago", "Off", "—"],
  ["P", "Pilot A1", "pilot-a-1.example", ["plain", "Not researched"], "Never", "Monthly", "—"],
  ["P", "Pilot A2", "pilot-a-2.example", ["plain", "Not researched"], "Never", "Off", "—"],
  ["P", "Pilot A3", "pilot-a-3.example", ["plain", "Not researched"], "Never", "Off", "—"],
];
screens.accounts = shell("acc", "<b>Accounts</b>", `
<div class="between" style="margin-bottom:6px"><h1>Accounts</h1><div class="row"><a class="btn">${ic.up} Import a file</a><a class="btn primary">${ic.plus} Add account</a></div></div>
<p class="sub" style="margin-bottom:22px">6 accounts in Synthetic Pilot A. Click an account to open its brief.</p>
<div class="between" style="margin-bottom:12px"><div class="seg"><span class="on">All 6</span><span>Has new signals 1</span><span>Running 1</span><span>Not researched 3</span></div><span class="small">Sorted by last researched</span></div>
<div class="panel" style="overflow:hidden">
<table class="t"><thead><tr><th>Account</th><th>Status</th><th>Platforms seen</th><th>Last research</th><th>Auto refresh</th><th></th></tr></thead><tbody>
${accRows.map((r, i) => `<tr><td><div class="row"><div class="logo ${i % 2 ? "lav" : ""}">${r[0]}</div><div><div style="font-weight:600">${r[1]}</div><div class="small">${r[2]}</div></div></div></td><td><span class="g ${r[3][0]}">${r[3][1]}</span></td><td>${r[6]}</td><td>${r[4]}</td><td><span class="select">${r[5]}</span></td><td class="num"><span class="icon-btn" style="width:30px;height:30px;display:inline-grid">${ic.more}</span></td></tr>`).join("")}
</tbody></table></div>
<p class="small" style="margin-top:14px">Auto refresh uses one research from your monthly allowance each time it runs. 2 of 2 researches left this month.</p>`);

/* ---------- 4. Account overview ---------- */
const acctHead = (tab) => `
<div class="between" style="margin-bottom:18px">
  <div class="row" style="gap:16px"><div class="logo lg">S</div><div><h1>Synthetic Holdings</h1><div class="sub">holdings-demo.example, researched 4 days ago from public sources and 1 uploaded file</div></div></div>
  <div class="row"><a class="btn">${ic.dl} Download files</a><a class="btn primary">${ic.ref} Refresh</a><span class="icon-btn">${ic.more}</span></div>
</div>
<div class="tabs" style="margin-bottom:24px">${["Overview", "People|6", "Jobs|640", "Technology|2", "News and signals|6", "Sources|3", "Files|19", "Activity"].map((t) => { const [n, c] = t.split("|"); return `<span class="${n === tab ? "on" : ""}">${n}${c ? `<b>${c}</b>` : ""}</span>`; }).join("")}</div>`;

screens.account = shell("acc", `Accounts / <b>Synthetic Holdings</b>`, `
${acctHead("Overview")}
<div style="display:grid;grid-template-columns:1fr 320px;gap:32px">
 <div>
  <h2 style="margin-bottom:10px">Summary</h2>
  <p style="font-size:15.5px;line-height:1.65;max-width:720px">Synthetic Holdings runs Salesforce as its CRM and SAP for finance <span class="cite">1</span>. A new CIO with an AI brief started in September <span class="cite">3</span>, and hiring has shifted to data platform and CRM roles <span class="cite">2</span>.</p>
  <div class="between" style="margin:30px 0 10px"><h2>Key findings</h2><a class="small" style="color:var(--primary);font-weight:600">See all 5 findings</a></div>
  <div class="panel">
   ${[["confirmed", "Confirmed", "New CIO with an AI brief", "Press release and LinkedIn post, 2 Sep"], ["probable", "Probable", "CRM harmonisation hiring across two regions", "12 job postings, Sep to Oct"], ["confirmed", "Confirmed", "Salesforce named as the CRM of record", "Careers page, 11 Oct"], ["hypothesis", "Hypothesis", "ERP consolidation may be funded next fiscal year", "Earnings call wording, Q2"]].map((f, i) => `<div class="between" style="padding:14px 18px;${i ? "border-top:1px solid var(--line)" : ""}"><div class="row" style="gap:14px"><span class="g ${f[0]}" style="width:104px">${f[1]}</span><div><div style="font-weight:600">${f[2]}</div><div class="small">${f[3]}</div></div></div><span class="small">3 Nov</span></div>`).join("")}
  </div>
  <p class="small" style="margin-top:12px">Say only Confirmed facts to a prospect. Probable and Hypothesis items are for your own preparation.</p>
 </div>
 <aside>
  <h3 style="margin-bottom:12px">At a glance</h3>
  <div class="panel" style="padding:4px 18px">
   ${[["People", "6"], ["Open job postings", "202 of 640"], ["News and signals", "6, 2 new"], ["Platforms seen", "Salesforce, SAP"]].map((r, i) => `<div class="between" style="padding:11px 0;${i ? "border-top:1px solid var(--line)" : ""}"><span class="sub">${r[0]}</span><b style="font-weight:600">${r[1]}</b></div>`).join("")}
  </div>
  <h3 style="margin:24px 0 10px">Research coverage</h3>
  <div class="panel" style="padding:16px 18px">
   <div class="between" style="margin-bottom:8px"><b>18 of 33 areas covered</b><span class="small">55%</span></div>
   <div class="meter"><i style="width:55%"></i></div>
   <p class="small" style="margin-top:10px">15 areas had no public source. They are listed under Sources with the reason.</p>
  </div>
  <h3 style="margin:24px 0 10px">Ask about this account</h3>
  <div class="input ph" style="height:44px">Who owns the Salesforce platform?</div>
 </aside>
</div>`);

/* ---------- 5. Account jobs ---------- */
const jobs = [["Salesforce Architect", "Salesforce", "Poznań, PL", "5 Oct 2026", "Open"], ["Data Platform Lead", "Data and AI", "Durham, US", "4 Oct 2026", "Open"], ["ServiceNow Developer", "ServiceNow", "Poznań, PL", "4 Oct 2026", "Closed"], ["Workday Analyst", "Other", "Philadelphia, US", "3 Oct 2026", "Open"], ["Snowflake Engineer", "Data and AI", "Bengaluru, IN", "2 Oct 2026", "Open"], ["Tableau Developer", "Tableau", "Brentford, UK", "1 Oct 2026", "Closed"], ["SAP FICO Consultant", "SAP", "Poznań, PL", "30 Sep 2026", "Open"], ["Veeva CRM Admin", "Veeva", "Durham, US", "29 Sep 2026", "Open"], ["Salesforce Business Analyst", "Salesforce", "Philadelphia, US", "28 Sep 2026", "Closed"], ["ML Engineer, Forecasting", "Data and AI", "Bengaluru, IN", "27 Sep 2026", "Open"]];
screens.jobs = shell("acc", `Accounts / Synthetic Holdings / <b>Jobs</b>`, `
${acctHead("Jobs")}
<div class="between" style="margin-bottom:14px">
  <div class="row" style="gap:8px"><div class="seg"><span class="on">All 640</span><span>Open 202</span><span>Posted this month 0</span></div><span class="select">Platform: All</span><span class="select">Location: All</span></div>
  <div class="row"><span class="small">Updated 4 days ago</span><a class="btn sm">${ic.dl} Download</a></div>
</div>
<div class="panel" style="overflow:hidden">
<table class="t"><thead><tr><th>Role</th><th>Platform</th><th>Location</th><th>Posted ▾</th><th>Status</th><th>Source</th></tr></thead><tbody>
${jobs.map((j) => `<tr><td style="font-weight:600">${j[0]}</td><td>${j[1]}</td><td>${j[2]}</td><td>${j[3]}</td><td><span class="g ${j[4] === "Open" ? "confirmed" : "plain"}">${j[4]}</span></td><td><a>Careers page</a></td></tr>`).join("")}
</tbody></table>
<div class="between" style="padding:10px 14px;border-top:1px solid var(--line)"><span class="small">Showing 1 to 10 of 640</span><div class="row" style="gap:6px"><span class="btn sm">Previous</span><span class="btn sm">Next</span></div></div>
</div>`);

/* ---------- 6. Account files ---------- */
screens.files = shell("acc", `Accounts / Synthetic Holdings / <b>Files</b>`, `
${acctHead("Files")}
<div style="display:grid;grid-template-columns:1fr 320px;gap:32px">
 <div>
  <div class="between" style="margin-bottom:10px"><h2>Research files</h2><a class="btn sm">${ic.dl} Download everything (.zip)</a></div>
  <div class="panel" style="margin-bottom:28px">
   ${[["Account summary", "Word document", "3 Nov, 214 KB"], ["People list", "Excel workbook, 6 people", "3 Nov, 48 KB"], ["Job postings", "Excel workbook, 640 rows", "3 Nov, 310 KB"], ["Tech stack and teams", "Excel workbook", "3 Nov, 62 KB"], ["Sources and evidence", "Word document", "3 Nov, 128 KB"]].map((f, i) => `<div class="between" style="padding:12px 18px;${i ? "border-top:1px solid var(--line)" : ""}"><div class="row"><span class="logo" style="width:30px;height:30px">${ic.file}</span><div><div style="font-weight:600">${f[0]}</div><div class="small">${f[1]}</div></div></div><div class="row"><span class="small">${f[2]}</span><a class="btn sm quiet">Open</a><a class="btn sm">${ic.dl}</a></div></div>`).join("")}
  </div>
  <div class="between" style="margin-bottom:10px"><h2>Your uploaded files</h2><a class="btn sm">${ic.plus} Add files</a></div>
  <div class="panel">
   <div class="between" style="padding:12px 18px"><div class="row"><span class="logo lav" style="width:30px;height:30px">${ic.folder}</span><div><div style="font-weight:600">Holdings_workspace.zip</div><div class="small">16 files read, 3 skipped (images without text)</div></div></div><div class="row"><span class="g confirmed">Ready</span><a class="btn sm quiet">View files</a><span class="icon-btn" style="width:30px;height:30px">${ic.more}</span></div></div>
  </div>
 </div>
 <aside>
  <h3 style="margin-bottom:10px">Who can see uploaded files</h3>
  <div class="panel" style="padding:16px 18px"><p class="small" style="color:var(--ink-2);line-height:1.6">Uploaded files stay in your workspace. They are used to answer your questions, and are never included in research files you share with a prospect.</p></div>
  <h3 style="margin:24px 0 10px">Earlier versions</h3>
  <div class="panel" style="padding:4px 18px">${[["3 Nov 2026", "Current"], ["14 Oct 2026", ""], ["2 Oct 2026", ""]].map((v, i) => `<div class="between" style="padding:10px 0;${i ? "border-top:1px solid var(--line)" : ""}"><span>${v[0]}</span>${v[1] ? `<span class="g plain">${v[1]}</span>` : `<a class="small" style="color:var(--primary);font-weight:600">Download</a>`}</div>`).join("")}</div>
 </aside>
</div>`);

/* ---------- 7. Research ---------- */
screens.research = shell("res", "<b>Research</b>", `
<h1 style="margin-bottom:6px">Research</h1>
<p class="sub" style="margin-bottom:24px">Start a research on a company, or upload your own file to analyse. You are told when it is ready.</p>
<div style="display:grid;grid-template-columns:560px 1fr;gap:36px">
 <div class="panel" style="padding:22px 24px">
  <div class="seg" style="margin-bottom:20px"><span class="on">Research a company</span><span>Analyse my file</span></div>
  <div class="stack" style="--g:16px">
   <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px"><div class="field"><label>Company</label><div class="input">Northwind Health</div></div><div class="field"><label>Website</label><div class="input">northwindhealth.com</div></div></div>
   <div class="field"><label>What should we focus on? <span style="color:var(--faint);font-weight:500">Optional</span></label><div class="input area ph">For example: we sell payroll software, so focus on their HR systems and hiring</div></div>
   <div class="between" style="padding:12px 14px;background:var(--canvas);border-radius:8px"><div><div style="font-weight:600;font-size:13.5px">Standard research</div><div class="small">Claude Opus 5.5, high effort. Change in Settings.</div></div><a class="small" style="color:var(--primary);font-weight:600">Change</a></div>
   <div class="between" style="margin-top:20px"><span class="small">About a working day. Uses 1 of 2 researches left.</span><a class="btn primary">Review and start</a></div>
  </div>
 </div>
 <div>
  <h2 style="margin-bottom:12px">Running now</h2>
  <div class="panel" style="padding:16px 18px;margin-bottom:28px">
   <div class="between"><div class="row"><div class="logo">S</div><div><div style="font-weight:600">Synthetic Signals</div><div class="small">Reading job postings, step 3 of 5</div></div></div><a class="btn sm">View progress</a></div>
   <div class="meter" style="margin-top:14px"><i style="width:46%"></i></div>
   <div class="between small" style="margin-top:8px"><span>Started 12 min ago</span><span>About 40 min left</span></div>
  </div>
  <h2 style="margin-bottom:12px">Recent</h2>
  <div class="panel">${[["Synthetic Holdings", "File analysis", "3 Nov", "confirmed", "Ready"], ["QuidelOrtho", "Full refresh", "17 Oct", "confirmed", "Ready"], ["Synthetic Holdings", "Company research", "14 Oct", "hypothesis", "Ready with gaps"], ["Pilot A2", "Company research", "10 Oct", "bad", "Stopped"]].map((r, i) => `<div class="between" style="padding:12px 18px;${i ? "border-top:1px solid var(--line)" : ""}"><div><div style="font-weight:600">${r[0]}</div><div class="small">${r[1]}, ${r[2]}</div></div><span class="g ${r[3]}">${r[4]}</span></div>`).join("")}</div>
 </div>
</div>`);

/* ---------- 8. Run progress ---------- */
const phases = [["Find the company", "Website, size and industry confirmed", "done"], ["Read public sources", "142 pages read: news, filings, careers", "done"], ["Read job postings", "640 postings found, 312 read so far", "run"], ["Grade every fact", "Each fact checked against its source", "todo"], ["Build your files", "Summary, people list, workbooks", "todo"]];
screens.run = shell("res", `Research / <b>Synthetic Signals</b>`, `
<div class="between" style="margin-bottom:22px"><div><h1>Researching Synthetic Signals</h1><p class="sub">Started 12 min ago. About 40 min left. We will email you when it is ready.</p></div><div class="row"><a class="btn">Open account</a><a class="btn danger">Stop research</a></div></div>
<div style="display:grid;grid-template-columns:1fr 320px;gap:32px">
 <div class="panel" style="padding:8px 22px">
  ${phases.map((p, i) => `<div class="row" style="align-items:flex-start;gap:16px;padding:16px 0;${i ? "border-top:1px solid var(--line)" : ""}">
    <span style="width:26px;height:26px;border-radius:50%;flex:none;display:grid;place-items:center;font-size:12px;font-weight:700;${p[2] === "done" ? "background:var(--mint);color:var(--mint-ink)" : p[2] === "run" ? "background:var(--primary);color:#fff" : "background:#EEF0F7;color:var(--faint)"}">${p[2] === "done" ? ic.check : i + 1}</span>
    <div style="flex:1"><div class="between"><b style="font-weight:600;${p[2] === "todo" ? "color:var(--muted)" : ""}">${p[0]}</b><span class="small">${p[2] === "done" ? "Done" : p[2] === "run" ? "In progress" : "Waiting"}</span></div><div class="small">${p[1]}</div>${p[2] === "run" ? '<div class="meter" style="margin-top:10px"><i style="width:48%"></i></div>' : ""}</div></div>`).join("")}
 </div>
 <aside>
  <h3 style="margin-bottom:10px">Found so far</h3>
  <div class="panel" style="padding:4px 18px">${[["Facts", "38"], ["Confirmed", "21"], ["People", "4"], ["Job postings", "312"]].map((r, i) => `<div class="between" style="padding:10px 0;${i ? "border-top:1px solid var(--line)" : ""}"><span class="sub">${r[0]}</span><b style="font-weight:600">${r[1]}</b></div>`).join("")}</div>
  <h3 style="margin:24px 0 10px">Usage</h3>
  <div class="panel" style="padding:16px 18px"><div class="between" style="margin-bottom:8px"><b>1.1M of about 2.8M tokens</b></div><div class="meter"><i style="width:39%"></i></div><p class="small" style="margin-top:10px">Estimated cost so far $4.90. We pause and ask you if it goes past 3 times the estimate.</p></div>
 </aside>
</div>`);

/* ---------- 9. Settings ---------- */
const models = [["Claude Opus 5.5", "Most thorough, slower", true], ["Claude Fable 5.1", "Fast and strong", false], ["Claude Sonnet 5.5", "Fast and economical", false], ["Claude Haiku 5.5", "Fastest, for simple work", false], ["Kimi K3", "By Moonshot AI", false]];
screens.settings = shell("set", "Settings / <b>Models</b>", `
<h1 style="margin-bottom:18px">Settings</h1>
<div class="tabs" style="margin-bottom:26px">${["Models", "Team", "Mail and drive", "Support access", "Usage"].map((t, i) => `<span class="${i === 0 ? "on" : ""}">${t}</span>`).join("")}</div>
<div style="max-width:860px">
 ${[["Research model", "Reads public sources and pulls out the facts. Used by new research, refresh and auto refresh.", "High"], ["Answer model", "Answers your questions, reads your files and writes the research files.", "Medium"]].map(([h, d, eff]) => `
 <div class="between" style="align-items:flex-start;margin-bottom:12px"><div><h2>${h}</h2><p class="sub" style="max-width:560px">${d}</p></div><div class="row"><span class="small">Effort</span><div class="seg"><span class="${eff === "Medium" ? "on" : ""}">Medium</span><span class="${eff === "High" ? "on" : ""}">High</span></div></div></div>
 <div class="panel" style="margin-bottom:30px">${models.map((m, i) => `<div class="between" style="padding:12px 18px;${i ? "border-top:1px solid var(--line)" : ""}"><div class="row" style="gap:14px"><span style="width:18px;height:18px;border-radius:50%;border:${m[2] ? "5px solid var(--primary)" : "1.5px solid var(--line-strong)"}"></span><div><div style="font-weight:600">${m[0]}</div><div class="small">${m[1]}</div></div></div>${m[2] ? '<span class="g lead">Workspace default</span>' : ""}</div>`).join("")}</div>`).join("")}
</div>`);

/* ---------- 10. Refresh dialog (confirm twice) ---------- */
screens.refresh = shell("acc", `Accounts / <b>Synthetic Holdings</b>`, `${acctHead("Overview")}<div style="opacity:.5"><h2>Summary</h2><p style="font-size:15.5px;margin-top:10px;max-width:720px">Synthetic Holdings runs Salesforce as its CRM and SAP for finance. A new CIO with an AI brief started in September.</p></div>`, {
  css: `.scrim{position:fixed;inset:0;background:rgba(27,33,64,.32);display:grid;place-items:center}.dlg{width:560px;background:#fff;border-radius:14px;padding:26px 28px;border:1px solid var(--line)}.opt{display:flex;gap:14px;align-items:flex-start;padding:14px 16px;border:1px solid var(--line);border-radius:10px}.opt.on{border-color:var(--primary);background:#F7F8FF}.radio{width:18px;height:18px;border-radius:50%;border:1.5px solid var(--line-strong);flex:none;margin-top:2px}.opt.on .radio{border:5px solid var(--primary)}`,
  overlay: `<div class="scrim"><div class="dlg">
  <h2 style="font-size:20px">Refresh Synthetic Holdings</h2>
  <p class="sub" style="margin:6px 0 18px">Last researched 4 days ago. A refresh looks only for what is new.</p>
  <div class="stack" style="--x:0">
   ${[["Job postings only", "New and closed roles", "About 3 min, $1.00", false], ["People and teams", "Senior hires, departures and team changes", "About 10 min, $3.00", true], ["Everything", "News, jobs, technology, people and speaking", "About 45 min, $12.50", false], ["One topic", "Tell us what to look into", "About 8 min, $2.00", false]].map((o) => `<div class="opt ${o[3] ? "on" : ""}"><span class="radio"></span><div style="flex:1"><div class="between"><b style="font-weight:600">${o[0]}</b><span class="small">${o[2]}</span></div><div class="small">${o[1]}</div></div></div>`).join("")}
  </div>
  <div style="margin-top:18px;padding:12px 14px;background:var(--butter);border-radius:8px;color:var(--butter-ink);font-size:13px">This uses 1 of your 2 researches left this month and goes to the public web.</div>
  <div class="between" style="margin-top:20px"><span class="small">Step 1 of 2: choose. Next you confirm the cost.</span><div class="row"><a class="btn quiet">Cancel</a><a class="btn primary">Continue</a></div></div>
 </div></div>`,
});

for (const [name, html] of Object.entries(screens)) writeFileSync(`${DIR}screens/${name}.html`, html);

const browser = await chromium.launch({ headless: true });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })).newPage();
for (const name of Object.keys(screens)) {
  await page.goto(`file://${DIR}screens/${name}.html`, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${DIR}png/${name}.png` });
  console.log("shot", name);
}
await browser.close();
