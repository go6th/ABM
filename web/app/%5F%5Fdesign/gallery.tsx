"use client";
// Sample content for the gallery only. It shows every primitive and workspace piece; the theme
// toggle switches the whole page. It is never imported by a production page.
import { useState } from "react";
import { Bell, Building2, Download, Globe, Plus, Search, Trash2 } from "lucide-react";
import * as Dialog from "@radix-ui/react-dialog";
import { Answer } from "@/components/ws/Conversation";
import { CitedText } from "@/components/ws/CitedText";
import { Composer } from "@/components/ws/Composer";
import { EvidenceDrawer } from "@/components/ws/EvidenceDrawer";
import type { Evidence } from "@/components/ws/evidence";
import { GradeMark, type Grade } from "@/components/ws/GradeMark";
import { UsedPanel } from "@/components/ws/UsedPanel";
import { WebOffer } from "@/components/ws/WebOffer";
import { CapMeter } from "@/components/ws/WorkspaceShell";
import { DataTable } from "@/ui/DataTable";
import { Input } from "@/ui/Input";
import { ThemeToggle } from "@/ui/ThemeToggle";
import type { ThemeChoice } from "@/ui/theme";
import { Badge, Banner, EmptyState, Kbd, LoadError, Meter, Monogram, PageHead, PageSkeleton, Tile } from "@/ui/primitives";
import { m } from "@/lib/messages";

const EVIDENCE: Evidence[] = [
  { n: 1, grade: "confirmed", reason: "Stated on the company's own careers page, retrieved this week.", title: "Cloud platform engineering lead", sourceName: "careers.haldenmedical.example", retrieved: "6 October", excerpt: "Lead the migration of clinical systems to our new cloud platform, working with the infrastructure and security teams.", span: [4, 58], url: "https://careers.haldenmedical.example/jobs/4411" },
  { n: 2, grade: "probable", reason: "Two recent postings name the same team and reporting line.", title: "Director, infrastructure and operations", sourceName: "Company announcement", retrieved: "2 October", excerpt: "Priya Raman joins as director of infrastructure and operations, reporting to the CIO.", span: [0, 11], url: "https://haldenmedical.example/news/infrastructure-director" },
  { n: 3, grade: "hypothesis", reason: "Our synthesis from hiring patterns. Confirm by asking who owns the migration budget.", title: "Migration likely funded from the 2026 capital plan", sourceName: "Annual report", retrieved: "28 September", excerpt: null, span: null, url: null },
];

const TEXT = "The cloud migration sits with the infrastructure and operations team [1]. Its new director, Priya Raman, joined this quarter and reports to the CIO [2]. The work appears to be funded from the 2026 capital plan [3], so she is the best first call.";
const TONES = ["peri", "mint", "peach", "lilac", "rose", "butter"] as const;
const GRADES: Grade[] = ["confirmed", "probable", "hypothesis", "lead"];

function G({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="g-section"><h2>{title}</h2>{children}</section>;
}

export function Gallery({ theme }: { theme: ThemeChoice }) {
  const [open, setOpen] = useState<number | null>(null);
  const [chip, setChip] = useState<HTMLButtonElement | null>(null);
  const [draft, setDraft] = useState("");
  const [seg, setSeg] = useState("recent");
  const [on, setOn] = useState(true);
  const onOpen = (n: number, el: HTMLButtonElement) => { setChip(el); setOpen(n); };
  return (
    <main className="app-main" id="main">
      <div className="content">
        <PageHead title="Design system" sub="Every primitive and workspace piece. Pastel on white, designed dark." actions={<ThemeToggle initial={theme} />} />

        <G title="Colour">
          <div className="g-row">
            {TONES.map((t) => (
              <div key={t} className="g-swatch"><span className={`fill-${t}`} />{t}</div>
            ))}
          </div>
          <div className="grid-3 block-gap">{TONES.slice(0, 6).map((t) => <Tile key={t} tone={t} label={`${t} tile`} value="128" note="Tabular figures" />)}</div>
        </G>

        <G title="Type">
          <div className="g-stack">
            <p className="t-large">Large title, 34</p>
            <p className="t-title1">Title 1, 28</p>
            <p className="t-title2">Title 2, 22</p>
            <p className="t-title3">Title 3, 20</p>
            <p className="t-headline">Headline, 17</p>
            <p>Body, 15. The quick brown fox reads the annual report.</p>
            <p className="t-reading">Reading, 16. Answers and claims use this size and leading.</p>
            <p className="t-subhead ink-2">Subhead, 14</p>
            <p className="t-footnote ink-2">Footnote, 13</p>
            <p className="t-caption ink-2">Caption, 12</p>
          </div>
        </G>

        <G title="Buttons">
          <div className="g-row">
            <button className="btn primary" type="button"><Plus size={16} aria-hidden="true" />Primary</button>
            <button className="btn" type="button">Default</button>
            <button className="btn tinted" type="button">Tinted</button>
            <button className="btn plain" type="button">Plain</button>
            <button className="btn quiet" type="button">Quiet</button>
            <button className="btn danger" type="button"><Trash2 size={16} aria-hidden="true" />Danger</button>
            <button className="btn web" type="button"><Globe size={16} aria-hidden="true" />Web</button>
            <button className="btn web-solid" type="button">Confirm refresh</button>
            <button className="btn" type="button" disabled>Disabled</button>
            <button className="btn sm" type="button">Small</button>
            <button className="btn lg primary" type="button">Large</button>
            <button className="icon-btn" type="button" aria-label="Download"><Download size={16} /></button>
          </div>
        </G>

        <G title="Fields and controls">
          <div className="grid-2">
            <Input label="Company domain" placeholder="example.com" hint="Adding an account is free." />
            <Input label="With an error" defaultValue="not a domain" error="Enter a web domain such as example.com." />
            <div className="field"><label htmlFor="g-sel">Select</label><select id="g-sel" className="select"><option>Recently refreshed</option><option>Name</option></select></div>
            <div className="field"><label htmlFor="g-ta">Text area</label><textarea id="g-ta" className="textarea" defaultValue="One per line" /></div>
          </div>
          <div className="g-row block-gap">
            <div className="search"><Search size={16} aria-hidden="true" /><input className="input" aria-label="Search accounts" placeholder="Search accounts" /></div>
            <div className="seg" role="group" aria-label="Sort">
              <button type="button" aria-pressed={seg === "recent"} onClick={() => setSeg("recent")}>Recently refreshed</button>
              <button type="button" aria-pressed={seg === "name"} onClick={() => setSeg("name")}>Name</button>
            </div>
            <label className="check"><input type="checkbox" defaultChecked />Checkbox</label>
            <label className="row">Switch <input className="switch" type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} /></label>
            <Kbd>Enter</Kbd>
          </div>
        </G>

        <G title="Badges, grades and chips">
          <div className="g-row">
            {TONES.map((t) => <Badge key={t} tone={t} dot>{t}</Badge>)}
            <Badge>Neutral</Badge>
          </div>
          <div className="g-row block-gap">{GRADES.map((g) => <GradeMark key={g} grade={g} />)}</div>
          <div className="g-row"><button className="chip" type="button">Filter chip</button><button className="chip" type="button" aria-pressed="true">Pressed chip</button><Monogram name="haldenmedical.example" /><span className="avatar">AR</span></div>
        </G>

        <G title="Banners and states">
          <div className="g-stack">
            <Banner kind="info" title="Information">Answers use data held in this workspace.</Banner>
            <Banner kind="success">Research preferences saved.</Banner>
            <Banner kind="warn">This is an unverified lead.</Banner>
            <Banner kind="error">The refresh could not start. Try again in a moment.</Banner>
            <Banner kind="web">Newer data may exist on the web.</Banner>
            <LoadError reference="req_7f2a" retryHref="/__design" />
            <EmptyState title={m.accounts.emptyTitle} body={m.accounts.emptyBody} icon={<Building2 size={20} strokeWidth={1.8} />} action={{ href: "/accounts#add", label: m.nav.add }} />
            <PageSkeleton />
            <p className="inline-status">Inline status</p>
            <p className="inline-error">Inline error</p>
            <span className="spinner" aria-label="Loading" />
          </div>
        </G>

        <G title="Meters and progress">
          <div className="g-stack">
            <Meter value={32} max={100} label="32%" />
            <Meter value={84} max={100} tone="warn" label="84%" />
            <Meter value={100} max={100} tone="full" label="100%" />
            <div className="slices" aria-hidden="true">{Array.from({ length: 7 }, (_, i) => <span key={i} data-done={i < 3} data-current={i === 3} />)}</div>
            <div className="g-row"><CapMeter used={12} limit={34} /><CapMeter used={30} limit={34} /><CapMeter used={34} limit={34} /></div>
          </div>
        </G>

        <G title="Lists and tables">
          <ul className="rows">
            {["haldenmedical.example", "northwind.example"].map((d, i) => (
              <li key={d}><a className="list-row" href="#main"><div className="lead"><Monogram name={d} /><div><p className="name">{d}</p><p className="context">{i ? "Not researched yet" : "Refreshed 2 days ago"}</p></div></div><div className="meta"><GradeMark grade={i ? "lead" : "confirmed"} /><span>{i ? "today" : "6 October"}</span></div></a></li>
            ))}
          </ul>
          <div className="block-gap">
            <DataTable caption="Sample table" columns={[{ key: "a", header: "Allowance", render: (r: { a: string; u: number }) => r.a }, { key: "u", header: "Used", numeric: true, render: (r) => r.u }]} rows={[{ a: "Accounts", u: 12 }, { a: "Refreshes", u: 40 }]} rowKey={(r) => r.a} />
          </div>
          <dl className="dl"><dt>Held here</dt><dd>12 facts</dd><dt>Web</dt><dd>Not used</dd></dl>
        </G>

        <G title="Conversation">
          <div className="reading">
            <p className="q">Who owns the cloud migration at Halden Medical?</p>
            <div className="a"><p><CitedText text={TEXT} evidence={EVIDENCE} pressed={open} onOpen={onOpen} /></p></div>
            <UsedPanel used={{ facts: 12, updated: "6 October", web: null, mail: null }} />
            <WebOffer offer={{ what: "Check the careers page and news for changes since 6 October.", scope: "Jobs and news", minutes: 3, credits: 2, left: 38 }} onConfirm={async () => undefined} onStay={() => undefined} />
            <div className="suggest">{m.home.suggestions.map((q) => <button key={q} type="button"><Bell size={16} aria-hidden="true" />{q}</button>)}</div>
            <div className="block-gap"><Answer text={"A **streaming** answer with a list:\n\n- First point\n- Second point"} streaming /></div>
            <Composer value={draft} onChange={setDraft} onSend={() => setDraft("")} streaming={false} placeholder={m.composer.allAccounts} large />
          </div>
          <EvidenceDrawer items={EVIDENCE} open={open} onOpenChange={setOpen} onPick={setOpen} returnFocus={chip} />
        </G>

        <G title="Dialog">
          <Dialog.Root>
            <Dialog.Trigger className="btn">Open a dialog</Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="overlay" />
              <Dialog.Content className="dialog">
                <Dialog.Title>Refresh from web</Dialog.Title>
                <Dialog.Description className="desc">This researches the account on the web and uses credits from your monthly pool.</Dialog.Description>
                <div className="actions"><Dialog.Close className="btn quiet">Stay local</Dialog.Close><Dialog.Close className="btn web-solid">Confirm refresh</Dialog.Close></div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        </G>
      </div>
    </main>
  );
}
