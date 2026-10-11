"use client";
// Sample content for the gallery only. It shows every signature component and state in both
// themes; it is never imported by a production page.
import { useState } from "react";
import { Answer } from "@/components/ws/Conversation";
import { CitedText } from "@/components/ws/CitedText";
import { Composer } from "@/components/ws/Composer";
import { EvidenceDrawer } from "@/components/ws/EvidenceDrawer";
import type { Evidence } from "@/components/ws/evidence";
import { GradeMark, type Grade } from "@/components/ws/GradeMark";
import { UsedPanel } from "@/components/ws/UsedPanel";
import { WebOffer } from "@/components/ws/WebOffer";
import { CapMeter } from "@/components/ws/WorkspaceShell";
import { Empty, LoadError, PageSkeleton } from "@/components/ws/States";
import { m } from "@/lib/messages";

const EVIDENCE: Evidence[] = [
  { n: 1, grade: "confirmed", reason: "Stated on the company's own careers page, retrieved this week.", title: "Cloud platform engineering lead", sourceName: "careers.haldenmedical.example", retrieved: "6 October", excerpt: "Lead the migration of clinical systems to our new cloud platform, working with the infrastructure and security teams.", span: [4, 58], url: "https://careers.haldenmedical.example/jobs/4411" },
  { n: 2, grade: "probable", reason: "Two recent postings name the same team and reporting line.", title: "Director, infrastructure and operations", sourceName: "Company announcement", retrieved: "2 October", excerpt: "Priya Raman joins as director of infrastructure and operations, reporting to the CIO.", span: [0, 11], url: "https://haldenmedical.example/news/infrastructure-director" },
  { n: 3, grade: "hypothesis", reason: "Our synthesis from hiring patterns. Confirm by asking who owns the migration budget.", title: "Migration likely funded from the 2026 capital plan", sourceName: "Annual report", retrieved: "28 September", excerpt: null, span: null, url: null },
];

const TEXT = "The cloud migration sits with the infrastructure and operations team [1]. Its new director, Priya Raman, joined this quarter and reports to the CIO [2]. The work appears to be funded from the 2026 capital plan [3], so she is the best first call.";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="g-section"><h2 className="ws-label ws-muted">{title}</h2>{children}</section>;
}

export function Gallery() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [open, setOpen] = useState<number | null>(null);
  const [chip, setChip] = useState<HTMLButtonElement | null>(null);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const onOpen = (n: number, el: HTMLButtonElement) => { setChip(el); setOpen(n); };
  return (
    <div className="ws g-root" id="ws-root" data-theme={theme}>
      <main className="ws-main">
        <div className="ws-column">
          <div className="ws-head">
            <div><h1 className="ws-display">Design system</h1><p className="sub">Every component and state, in both themes.</p></div>
            <div className="ws-seg" role="group" aria-label="Theme">
              <button type="button" aria-pressed={theme === "light"} onClick={() => setTheme("light")}>Light</button>
              <button type="button" aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}>Dark</button>
            </div>
          </div>

          <Section title="Grade marks">
            <div className="g-row">{(["confirmed", "probable", "hypothesis", "lead"] as Grade[]).map((g) => <GradeMark key={g} grade={g} />)}</div>
          </Section>

          <Section title="Answer with citations">
            <p className="ws-q">Who in their cloud migration team should I contact?</p>
            <div className="ws-a ws-prose"><p><CitedText text={TEXT} evidence={EVIDENCE} pressed={open} onOpen={onOpen} /></p></div>
            <UsedPanel used={{ facts: 41, updated: "6 October", web: null, mail: null }} />
            <WebOffer
              offer={{ what: "Job postings were last refreshed 6 October. Newer postings may exist.", scope: "Job postings only", minutes: 4, credits: 6, left: 184 }}
              onConfirm={() => new Promise((r) => setTimeout(r, 1200))}
              onStay={() => undefined}
            />
          </Section>

          <Section title="Streaming answer">
            <Answer text={"The infrastructure team is hiring for three cloud roles"} streaming />
          </Section>

          <Section title="Composer">
            <Composer value={draft} onChange={setDraft} onSend={() => setStreaming(true)} onStop={() => setStreaming(false)} streaming={streaming} placeholder={m.composer.account("Halden Medical Group")} />
          </Section>

          <Section title="Buttons">
            <div className="g-row">
              <button type="button" className="ws-btn primary">{m.nav.add}</button>
              <button type="button" className="ws-btn web">{m.workspace.refresh}</button>
              <button type="button" className="ws-btn">{m.state.retry}</button>
              <button type="button" className="ws-btn quiet">{m.refresh.stay}</button>
              <button type="button" className="ws-btn primary" disabled>{m.nav.add}</button>
            </div>
          </Section>

          <Section title="Account cap">
            <div className="g-row"><CapMeter used={12} limit={34} /><CapMeter used={34} limit={34} /></div>
          </Section>

          <Section title="Tags">
            <div className="g-row"><span className="ws-tag spruce">{m.accounts.library}</span><span className="ws-tag">Weekly</span><span className="ws-tag ochre">Web refresh</span><span className="ws-tag brick">Skipped</span></div>
          </Section>

          <Section title="Run progress">
            <div className="ws-status"><p className="ws-label">{m.workspace.runTitle}</p><p className="ws-small ws-muted">{m.workspace.runStep(3, 7)}, 2 min 14 s</p>
              <div className="ws-progress" aria-hidden="true">{Array.from({ length: 7 }, (_, i) => <span key={i} data-done={i < 3} data-current={i === 3} />)}</div></div>
          </Section>

          <Section title="States">
            <div className="g-stack">
              <Empty title={m.accounts.emptyTitle} body={m.accounts.emptyBody} action={{ href: "#", label: m.nav.add }} />
              <LoadError reference="7f3a91c2" retryHref="#" />
              <div className="ws-banner web"><span className="grow">{m.refresh.noCredits}</span></div>
              <div className="ws-banner"><span className="grow">{m.state.offline}</span></div>
              <PageSkeleton />
            </div>
          </Section>
        </div>
      </main>
      <EvidenceDrawer items={EVIDENCE} open={open} onOpenChange={setOpen} onPick={setOpen} returnFocus={chip} />
    </div>
  );
}
