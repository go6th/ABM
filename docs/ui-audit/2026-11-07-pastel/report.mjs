// Builds the 2026-11-07 client UI audit and pastel redesign report from the screenshots that
// shots.mjs took (after/) and the earlier captures (before/), and prints it to PDF.
// Usage: node report.mjs <after-dir> <out.pdf>
import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { FINDINGS, NEXT_PROMPT } from "./findings.mjs";
const require = createRequire("/home/sakib/ABM Generator/web/package.json");
const { chromium } = require("@playwright/test");

const DIR = decodeURIComponent(new URL(".", import.meta.url).pathname);
const AFTER = process.argv[2];
const OUT = process.argv[3];
const img = (path) => `file://${encodeURI(path)}`;
const after = (n) => img(`${AFTER}/${n}.jpg`);
const before = (n) => img(`${DIR}before/${n}.jpg`);

let pageNo = 0;
const page = (body, cls = "") => {
  pageNo += 1;
  return `<section class="pg ${cls}">${body}<footer><span>Client app: UI audit and pastel redesign · 7 Nov 2026</span><span>${pageNo}</span></footer></section>`;
};
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
const tag = (status) => `<span class="tag ${status.toLowerCase().replace(/\s+/g, "-")}">${status}</span>`;

const total = FINDINGS.reduce((n, g) => n + g.items.length, 0);
const fixed = FINDINGS.reduce((n, g) => n + g.items.filter((i) => i.status === "Fixed" || i.status === "Removed").length, 0);

const cover = page(`
  <div class="cover">
    <p class="kicker">Account Intelligence · client web app</p>
    <h1>UI audit and pastel redesign</h1>
    <p class="lead">A full audit of the client screens (refresh, pop-ups, buttons, errors and clutter) and a
    professional pastel look with depth, glow on the next step and feedback on every click. The admin
    console is unchanged, as asked.</p>
    <div class="stats">
      <div><b>${total}</b><span>problems found</span></div>
      <div><b>${fixed}</b><span>fixed or removed</span></div>
      <div><b>${total - fixed}</b><span>kept on purpose or open</span></div>
      <div><b>21</b><span>client screens checked</span></div>
    </div>
    <img class="hero" src="${after("account-1440")}">
  </div>`, "cover-pg");

const design = page(`
  <h2>What changed in the look</h2>
  <div class="cols">
    <ul class="points">
      <li><b>Pastel palette.</b> White and soft periwinkle surfaces over a faint lavender, blue and mint wash. Mint, butter and rose are kept for status only.</li>
      <li><b>Depth, not decoration.</b> Cards, the sidebar and the account header are raised with layered soft shadows and a thin top highlight. Nothing new was added to the screens.</li>
      <li><b>One main button per screen.</b> Periwinkle-to-lavender gradient with a soft glow. The next step (Ask, Start research, Yes, start) breathes a gentle glow so the eye finds it.</li>
      <li><b>Every click answers.</b> Buttons press in and change colour; a thin glowing bar runs across the top while the next page loads; the tab or link you clicked shows it is opening; a button that is working shows a small spinner.</li>
      <li><b>Pop-ups that behave.</b> One shared pop-up: opens with a short scale-in, keeps keyboard focus inside, closes on Escape or a backdrop click, returns focus, and cannot close while a paid start is being sent.</li>
      <li><b>Short notes after actions.</b> "Refresh started", "Chat deleted", "Auto refresh saved" appear briefly at the bottom and are read out by screen readers.</li>
      <li><b>Motion stays light.</b> Only opacity and transform are animated; everything stops for people who ask their system for reduced motion.</li>
    </ul>
    <div class="stack">
      <img src="${after("feedback-tab-click")}"><p class="cap">Click feedback: the top bar and the pending tab, 70 ms after a click.</p>
      <img src="${after("menu-more")}"><p class="cap">The More menu: it names the open page, closes on Escape, an outside click or a page change.</p>
    </div>
  </div>`);

const spend = page(`
  <h2>Spending money: two presses, no extra screens</h2>
  <p class="lead small">The owner's rule stays: every research, refresh and file analysis is confirmed twice before money is spent,
  and the server still refuses a start without both. What changed is how it feels: the cost is shown up front, the first
  press arms the button (it glows and asks "Are you sure?"), the second press starts. Before, it took three presses across
  three different screens of text.</p>
  <div class="pair">
    <figure><img src="${after("popup-refresh")}"><figcaption>Refresh pop-up: cost of the picked option above one button.</figcaption></figure>
    <figure><img src="${after("popup-refresh-armed")}"><figcaption>After the first press: "Are you sure?" and the glowing Yes.</figcaption></figure>
  </div>`);

const beforeAfter = ["ask", "accounts", "account", "research"].map((n) => page(`
  <h2>Before and after: ${{ ask: "Ask", accounts: "Accounts", account: "Account overview", research: "Research" }[n]}</h2>
  <div class="pair">
    <figure><img src="${before(n)}"><figcaption>Before</figcaption></figure>
    <figure><img src="${after(`${n}-1440`)}"><figcaption>After</figcaption></figure>
  </div>`)).join("");

const chunks = (list, n) => Array.from({ length: Math.ceil(list.length / n) }, (_, i) => list.slice(i * n, i * n + n));
const findings = FINDINGS.flatMap((g) => chunks(g.items, 8).map((items, i, all) => ({ ...g, items, title: all.length > 1 ? `${g.title} (${i + 1} of ${all.length})` : g.title }))).map((g) => page(`
  <h2>${esc(g.title)}</h2>
  <p class="lead small">${esc(g.intro)}</p>
  <table class="f">
    <thead><tr><th>What the user saw</th><th>Cause</th><th>Now</th><th></th></tr></thead>
    <tbody>${g.items.map((i) => `<tr><td>${esc(i.saw)}</td><td>${esc(i.cause)}</td><td>${esc(i.now)}</td><td>${tag(i.status)}</td></tr>`).join("")}</tbody>
  </table>`, "dense")).join("");

const gallery = [
  ["Every screen, desktop (1)", ["signin-1440", "chats-1440", "people-1440", "jobs-1440"]],
  ["Every screen, desktop (2)", ["technology-1440", "sources-1440", "files-1440", "settings-1440"]],
  ["Every screen, desktop (3)", ["auto-refresh-1440", "team-1440", "mail-drive-1440", "popup-delete"]],
].map(([title, names]) => page(`<h2>${title}</h2><div class="grid4">${names.map((n) => `<figure><img src="${after(n)}"><figcaption>${n.replace(/-1440$/, "").replace(/-/g, " ")}</figcaption></figure>`).join("")}</div>`)).join("");

const phone = page(`<h2>Phone width (390 px)</h2><div class="phones">${["ask", "accounts", "account", "research"].map((n) => `<figure><img src="${after(`${n}-390`)}"><figcaption>${n}</figcaption></figure>`).join("")}</div>`);

const next = page(`
  <h2>Left for later, and a prompt to continue</h2>
  <ul class="points">
    <li><b>Admin console.</b> Not touched, as asked. It keeps its current look until you want it redesigned.</li>
    <li><b>Workspace names on sign-in.</b> The sign-in picker now shows "Workspace 1a2b" and the role in words; real names need the API to send them.</li>
    <li><b>Kept on purpose.</b> The per-answer token line, the context ring and the token card are the owner's designs. The More tables (Calls made, Searches run, Notes) stay because clients receive them in their files.</li>
  </ul>
  <h3>Prompt to paste when you want the next round</h3>
  <pre class="prompt">${esc(NEXT_PROMPT)}</pre>`);

const css = `
@page { size: 297mm 210mm; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; font-family: "Hanken Grotesk", system-ui, sans-serif; color: #191E3A; }
.pg { width: 297mm; height: 210mm; padding: 14mm 16mm 12mm; position: relative; page-break-after: always; overflow: hidden;
  background: #F5F6FD radial-gradient(900px 500px at 100% -10%, rgba(198,184,252,.30), transparent 60%); }
footer { position: absolute; left: 16mm; right: 16mm; bottom: 6mm; display: flex; justify-content: space-between; font-size: 9pt; color: #586082; }
h1 { font-size: 34pt; letter-spacing: -0.03em; margin: 4mm 0 3mm; }
h2 { font-size: 19pt; letter-spacing: -0.02em; margin: 0 0 5mm; }
h3 { font-size: 12pt; margin: 5mm 0 2mm; }
.kicker { font-size: 10pt; font-weight: 700; color: #5B3FC4; text-transform: uppercase; letter-spacing: .06em; margin: 0; }
.lead { font-size: 12pt; line-height: 1.5; color: #394166; max-width: 220mm; margin: 0 0 5mm; }
.lead.small { font-size: 10.5pt; }
.stats { display: flex; gap: 6mm; margin: 4mm 0 6mm; }
.stats div { background: #fff; border: 1px solid #E4E7F4; border-radius: 4mm; padding: 4mm 6mm; box-shadow: 0 8px 20px -10px rgba(48,56,140,.25); }
.stats b { display: block; font-size: 22pt; color: #4B5BE6; }
.stats span { font-size: 9.5pt; color: #586082; }
.hero { width: 150mm; position: absolute; right: 14mm; bottom: 16mm; border-radius: 3mm; box-shadow: 0 20px 40px -16px rgba(48,56,140,.4); border: 1px solid #E4E7F4; }
img { max-width: 100%; display: block; border-radius: 2mm; border: 1px solid #E4E7F4; }
.cols { display: grid; grid-template-columns: 1.05fr 1fr; gap: 8mm; }
.points { margin: 0; padding-left: 5mm; display: flex; flex-direction: column; gap: 2.4mm; font-size: 10.5pt; line-height: 1.45; color: #394166; }
.points b { color: #191E3A; }
.stack { display: flex; flex-direction: column; gap: 2mm; }
.cap, figcaption { font-size: 9pt; color: #586082; margin: 1mm 0 2mm; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 6mm; }
.pair img { height: 140mm; width: 100%; object-fit: cover; object-position: top center; }
figure { margin: 0; }
.grid4 { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }
.grid4 img { height: 70mm; width: 100%; object-fit: cover; object-position: top left; }
.phones { display: grid; grid-template-columns: repeat(4, 1fr); gap: 5mm; }
.phones img { height: 150mm; width: 100%; object-fit: cover; object-position: top; }
table.f { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 8.6pt; line-height: 1.35; background: #fff; border: 1px solid #E4E7F4; border-radius: 3mm; overflow: hidden; }
table.f th { text-align: left; background: #F1F3FE; color: #586082; font-weight: 700; padding: 2mm 3mm; }
table.f td { padding: 1.8mm 3mm; border-top: 1px solid #EDEFF8; vertical-align: top; }
table.f td:nth-child(1) { width: 34%; } table.f td:nth-child(2) { width: 28%; color: #586082; } table.f td:nth-child(4) { width: 18mm; }
.tag { display: inline-block; padding: .6mm 2.2mm; border-radius: 9mm; font-weight: 700; font-size: 8pt; white-space: nowrap; }
.tag.fixed, .tag.removed { background: #E3F5EE; color: #17694A; }
.tag.kept { background: #F0EAFF; color: #5B3FC4; }
.tag.open { background: #FFF4D6; color: #7A5A00; }
.prompt { white-space: pre-wrap; font-size: 8.4pt; line-height: 1.45; background: #fff; border: 1px solid #E4E7F4; border-radius: 3mm; padding: 4mm; color: #191E3A; }
`;

const html = `<!doctype html><html><head><meta charset="utf-8"><title>Client UI audit and pastel redesign</title>
<style>@font-face { font-family: "Hanken Grotesk"; font-weight: 400; src: url("file:///home/sakib/ABM%20Generator/web/ui/fonts/hanken-grotesk-latin-400-normal.woff2"); }
@font-face { font-family: "Hanken Grotesk"; font-weight: 600; src: url("file:///home/sakib/ABM%20Generator/web/ui/fonts/hanken-grotesk-latin-600-normal.woff2"); }
@font-face { font-family: "Hanken Grotesk"; font-weight: 700; src: url("file:///home/sakib/ABM%20Generator/web/ui/fonts/hanken-grotesk-latin-700-normal.woff2"); }
${css}</style></head><body>${cover}${design}${spend}${beforeAfter}${findings}${gallery}${phone}${next}</body></html>`;
writeFileSync(`${DIR}report.html`, html);
const browser = await chromium.launch();
const p = await browser.newPage();
await p.goto(`file://${encodeURI(DIR)}report.html`, { waitUntil: "load" });
await p.pdf({ path: OUT, width: "297mm", height: "210mm", printBackground: true });
await browser.close();
console.log(`${pageNo} pages -> ${OUT}`);
