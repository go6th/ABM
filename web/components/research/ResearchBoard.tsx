"use client";
// The Research page (redesign 2026-11-08, owner: "design the research page"; accounts are added
// and files imported on Accounts, so this page is only about research). The head says what the
// page is for and opens "Start new research" as a pop-up. Below it, every web research running now
// as a card: the company, a progress bar with the step it is on, when it started, and Show steps,
// Open account and Stop. Under them, the research finished in the last two weeks as a quiet list
// with how each one ended. The research shown with its steps is in the address (?r=<job id>), so a
// reload or a shared link ("Watch it live" on an account) opens the same one; with none chosen the
// server shows the first one running. Token counts and the findings feed are gone from this page
// (the account page is where results are read). Everything comes from the server's live stream
// (useResearchLive); the browser stores nothing.
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ResearchCompany } from "@/components/accounts/ResearchCompany";
import { ResumeButton, resumingLine } from "@/components/accounts/ResumeResearch";
import { StopButton } from "@/components/ws/RunSteps";
import { api, errorText } from "@/lib/api";
import type { ResearchEstimate, ResearchLive, ResearchResumed, ResearchStarted } from "@/lib/gen/api.gen";
import { m } from "@/lib/messages";
import { useResearchLive, type LiveState } from "@/lib/useResearchLive";
import { Modal } from "@/ui/Modal";
import { Pill, StateCard, type PillTone } from "@/ui/design";

type Item = ResearchLive["running"][number];
type Detail = NonNullable<ResearchLive["detail"]>;

const STATE_LABEL: Record<string, string> = {
  done: m.run.done,
  running: m.run.running,
  waiting: m.run.waiting,
  paused: m.run.paused,
  stopped: m.run.stepStopped,
  not_reached: m.run.notReached,
};

/** "2 min ago", "just now": from an ISO time and the current time (null until mounted). */
export function ago(iso: string | null | undefined, now: number | null): string | null {
  if (!iso || now === null) return null;
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 10) return "just now";
  if (s < 60) return `${s} s ago`;
  const min = Math.round(s / 60);
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

function useNow(every = 15000): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    // the clock starts after mount, so the server's render and the first client render agree
    const first = setTimeout(() => setNow(Date.now()), 0);
    const t = setInterval(() => setNow(Date.now()), every);
    return () => { clearTimeout(first); clearInterval(t); };
  }, [every]);
  return now;
}

/** The run Resume research is offered on for this item (the server's ``can_resume``), or null. */
export function resumableRun(item: Item | undefined): string | null {
  return item && !item.open && item.can_resume && item.run_id ? item.run_id : null;
}

/** How a finished research ended, in a word and a tone (the pill on its row). */
export function outcome(item: Item): { text: string; tone: PillTone } {
  if (item.run_status === "PARTIAL") return { text: m.board.partial, tone: "warn" };
  if (item.status === "done") return { text: m.board.complete, tone: "ok" };
  if (item.status === "canceled") return { text: m.board.cancelled, tone: "neutral" };
  if (item.status === "failed") return { text: m.board.didNotFinish, tone: "bad" };
  return { text: item.label, tone: "neutral" };
}

/** "New research", "Refresh from web, job postings: pricing". */
function kindLine(item: Item): string {
  const scope = item.scope !== "Everything" ? `, ${item.scope.toLowerCase()}` : "";
  return `${item.kind}${scope}${item.topic ? `: ${item.topic}` : ""}`;
}

function initialOf(name: string): string {
  return (name.replace(/^www\./i, "").match(/[a-z0-9]/i)?.[0] ?? "?").toUpperCase();
}

function Connection({ state }: { state: LiveState }) {
  const text = state === "live" ? m.board.live : state === "polling" ? m.board.polling : m.board.connecting;
  // only the connection state is a live region; it changes rarely, so it is read out only then
  return (
    <span className="live-state" data-state={state}>
      <span className="pdot" aria-hidden="true" />
      <span role="status" aria-live="polite">{text}</span>
    </span>
  );
}

function Steps({ detail }: { detail: Detail }) {
  if (!detail.steps.length) return <p className="mut sm-text">{detail.label}</p>;
  return (
    <ol className="steps rs-steps" aria-label={m.board.steps}>
      {detail.steps.map((st, i) => (
        <li key={st.id} data-state={st.state} aria-current={st.state === "running" || st.state === "paused" ? "step" : undefined}>
          <span className="n">{i + 1}</span>
          <span className="grow">{st.name}</span>
          <span className="state">{st.state === "running" ? <><span className="spinner" aria-hidden="true" /> </> : null}{STATE_LABEL[st.state] ?? st.state}</span>
        </li>
      ))}
    </ol>
  );
}

/** The opened research's steps (and why it stopped, when it did), under its card or row. */
function Opened({ detail }: { detail: Detail }) {
  const id = `rs-detail-${detail.job_id}`;
  return (
    <section className="rs-detail stack-10" aria-labelledby={id}>
      <h3 id={id} className="sr-only">{detail.company}</h3>
      <div className="rs-detail-head">
        <span className="strong">{m.board.steps}</span>
        <span className="mut sm-text">{detail.label}</span>
      </div>
      {detail.reason ? <p className="notice warn sm-text">{m.board.reason(detail.reason)}</p> : null}
      <Steps detail={detail} />
    </section>
  );
}

function RunningCard({ item, detail, now, onToggle, onStop }: {
  item: Item;
  detail: Detail | null;
  now: number | null;
  onToggle: (item: Item, open: boolean) => void;
  onStop: (item: Item) => Promise<void>;
}) {
  const open = detail !== null;
  const total = item.step?.total ?? item.steps_total;
  const done = Math.min(item.steps_done, total);
  const when = ago(item.started_at ?? item.created_at, now);
  const stepLine = item.step ? m.board.stepOf(item.step.index, item.step.total, item.step.name) : item.label;
  return (
    <li className="card rs-card" data-open={open || undefined}>
      <div className="rs-top">
        <span className="ti" aria-hidden="true">{initialOf(item.company)}</span>
        <div className="rs-name">
          <b>{item.company}</b>
          <span className="mut xs-text">{kindLine(item)}</span>
        </div>
        <Pill tone="run" small>{m.board.researching}</Pill>
      </div>
      <div className="rs-progress">
        <div className="rs-progress-line">
          <span className="rs-step">{stepLine}</span>
          {total ? <span className="mut xs-text num">{m.board.stepsDone(done, total)}</span> : null}
        </div>
        {total ? (
          <progress className="pbar rs-bar" max={total} value={done} aria-label={`${item.company}: ${m.board.stepsDone(done, total)}`} />
        ) : null}
      </div>
      <div className="rs-foot">
        <span className="mut xs-text">{when ? m.board.started(when) : m.board.startingSoon}</span>
        <span className="rs-actions">
          <button type="button" className="btn sm" aria-expanded={open} aria-label={`${open ? m.board.hideSteps : m.board.showSteps}: ${item.company}`} onClick={() => onToggle(item, !open)}>
            {open ? m.board.hideSteps : m.board.showSteps}
          </button>
          <Link className="btn sm" href={`/accounts/${encodeURIComponent(item.domain)}`} prefetch={false}>{m.board.openAccount}</Link>
          {item.can_stop ? <StopButton onStop={() => onStop(item)} label={m.board.stop} /> : null}
        </span>
      </div>
      {detail ? <Opened detail={detail} /> : null}
    </li>
  );
}

function RecentRow({ item, detail, now, onToggle, onResumed }: {
  item: Item;
  detail: Detail | null;
  now: number | null;
  onToggle: (item: Item, open: boolean) => void;
  onResumed: (resumed: ResearchResumed) => void;
}) {
  const open = detail !== null;
  const end = outcome(item);
  const when = ago(item.finished_at, now);
  const resumeRun = resumableRun(item);
  return (
    <li className="rs-row" data-open={open || undefined}>
      <div className="rs-row-main">
        <span className="ti lav" aria-hidden="true">{initialOf(item.company)}</span>
        <div className="rs-name">
          <b>{item.company}</b>
          <span className="mut xs-text">{kindLine(item)}</span>
        </div>
        <span className="rs-end"><Pill tone={end.tone} small>{end.text}</Pill></span>
        <span className="rs-when mut xs-text">{when ? m.board.finished(when) : null}</span>
        <span className="rs-actions">
          {resumeRun ? <ResumeButton key={resumeRun} runId={resumeRun} compact onResumed={onResumed} /> : null}
          <button type="button" className="btn sm" aria-expanded={open} aria-label={`${open ? m.board.hideSteps : m.board.showSteps}: ${item.company}`} onClick={() => onToggle(item, !open)}>
            {open ? m.board.hideSteps : m.board.showSteps}
          </button>
          <Link className="btn sm" href={`/accounts/${encodeURIComponent(item.domain)}`} prefetch={false}>{m.board.openAccount}</Link>
        </span>
      </div>
      {detail ? <Opened detail={detail} /> : null}
    </li>
  );
}

export function ResearchBoard({ initial, estimate, startOpen = false }: { initial: ResearchLive | null; estimate: ResearchEstimate | null; startOpen?: boolean }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const selected = params.get("r");
  const { data, state, restart } = useResearchLive(initial, selected);
  const now = useNow(state === "live" ? 5000 : 15000);
  const [starting, setStarting] = useState(startOpen);
  // the research whose steps were closed by hand (the server still sends it as the detail)
  const [closed, setClosed] = useState<string | null>(null);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);

  const select = useCallback((id: string) => {
    setClosed(null);
    router.replace(`${pathname}?r=${encodeURIComponent(id)}`, { scroll: false });
  }, [router, pathname]);

  const toggle = useCallback((item: Item, open: boolean) => {
    if (open) select(item.job_id);
    else setClosed(item.job_id);
  }, [select]);

  const stop = useCallback(async (item: Item) => {
    setNote(null);
    try {
      await api("POST", `/v1/research/${encodeURIComponent(item.job_id)}/stop`);
      setNote({ text: m.board.stopped });
    } catch (cause) {
      // 409 (the research has ended) and 503 (a worker holds it) come with a reason when the API
      // wrote one; a bare status text gives the plain sentence
      setNote({ text: errorText(cause, m.board.stopFailed), bad: true });
    }
    restart();
  }, [restart]);

  const resumed = useCallback((r: ResearchResumed) => {
    setNote({ text: `${resumingLine(r.steps_done, r.steps_total)}. You will be notified when it finishes.` });
    select(r.job_id);
    restart();
  }, [select, restart]);

  const started = useCallback((s: ResearchStarted) => {
    setStarting(false);
    setNote({ text: m.board.startedNote });
    select(s.job_id);
    restart();
  }, [select, restart]);

  const head = (
    <div className="head">
      <div className="ttl">
        <h1>{m.board.title}</h1>
        <div className="sub">{m.board.sub} <Link className="link" href="/accounts" prefetch={false}>{m.board.toAccounts}</Link></div>
      </div>
      <div className="row-12 wrap">
        {data ? <Connection state={state} /> : null}
        <button type="button" className="btn primary" aria-haspopup="dialog" onClick={() => setStarting(true)}>{m.board.start}</button>
      </div>
    </div>
  );

  const startDialog = starting ? (
    <Modal title={m.board.startTitle} wide onClose={() => setStarting(false)}>
      <p className="mut sm-text">{m.board.startIntro}</p>
      <ResearchCompany estimate={estimate} idPrefix="rb" onStarted={started} />
    </Modal>
  ) : null;

  if (!data) {
    // a full reload: a client navigation to the same address keeps this board (and its empty
    // state), so the button would seem to do nothing
    return (
      <div className="stack-22">
        {head}
        <StateCard title={m.board.title} body={m.board.loadFailed} action={<a className="btn" href={selected ? `/research?r=${encodeURIComponent(selected)}` : "/research"}>{m.run.refresh}</a>} />
        {startDialog}
      </div>
    );
  }
  const shown = data.detail && data.detail.job_id !== closed ? data.detail : null;
  const detailFor = (item: Item) => (shown && shown.job_id === item.job_id ? shown : null);

  return (
    <div className="stack-22">
      {head}

      {note ? <p className={`notice ${note.bad ? "error" : "info"} sm-text`} role={note.bad ? "alert" : "status"}>{note.text}</p> : null}

      <section className="stack-12" aria-labelledby="research-running-title">
        <div className="rs-sec-head">
          <h2 id="research-running-title">{m.board.running}</h2>
          {data.running.length ? <span className="rs-count">{data.running.length}</span> : null}
        </div>
        {data.running.length ? (
          <ul className="rs-cards" aria-label={m.board.running}>
            {data.running.map((item) => (
              <RunningCard key={item.job_id} item={item} detail={detailFor(item)} now={now} onToggle={toggle} onStop={stop} />
            ))}
          </ul>
        ) : (
          <div className="card rs-empty">
            <div className="stack-4">
              <span className="strong">{m.board.noneRunningTitle}</span>
              <span className="mut sm-text">{m.board.noneRunning}</span>
            </div>
            <button type="button" className="btn" aria-haspopup="dialog" onClick={() => setStarting(true)}>{m.board.start}</button>
          </div>
        )}
      </section>

      <section className="stack-12" aria-labelledby="research-recent-title">
        <div className="rs-sec-head">
          <h2 id="research-recent-title">{m.board.recent}</h2>
          {data.recent.length ? <span className="rs-count">{data.recent.length}</span> : null}
        </div>
        {data.recent.length ? (
          <ul className="card clip rs-list" aria-label={m.board.recent}>
            {data.recent.map((item) => (
              <RecentRow key={item.job_id} item={item} detail={detailFor(item)} now={now} onToggle={toggle} onResumed={resumed} />
            ))}
          </ul>
        ) : <p className="mut sm-text">{m.board.noneRecent}</p>}
      </section>

      {startDialog}
    </div>
  );
}
