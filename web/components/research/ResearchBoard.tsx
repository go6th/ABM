"use client";
// The Research screen (owner request 2026-10-21): every web research running now and finished
// recently on one page, Start and Stop, and the selected research in detail: its steps, what it
// has found so far by kind, and its newest findings as they are saved. Everything comes from the
// server's live stream (useResearchLive); the browser stores nothing. The selected research is in
// the address (?r=<job id>), so a reload or a shared link shows the same one.
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ResearchCompany } from "@/components/accounts/ResearchCompany";
import { ResumeButton, resumingLine } from "@/components/accounts/ResumeResearch";
import { StopButton } from "@/components/ws/RunSteps";
import { formatTokens } from "@/components/ws/TokenMeter";
import { api, ApiError } from "@/lib/api";
import type { ResearchEstimate, ResearchLive, ResearchResumed, ResearchStarted } from "@/lib/gen/api.gen";
import { m } from "@/lib/messages";
import { useResearchLive, type LiveState } from "@/lib/useResearchLive";
import { Grade, StateCard } from "@/ui/design";

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

function where(item: Item): string {
  if (item.step) return m.board.stepOf(item.step.index, item.step.total, item.step.name);
  return item.label;
}

function tone(item: Item): "run" | "ok" | "warn" | "" {
  if (item.open) return "run";
  if (item.status === "done" && item.run_status === "COMPLETED") return "ok";
  if (item.status === "failed" || item.status === "canceled" || item.run_status === "PARTIAL") return "warn";
  return "";
}

/** The run Resume research is offered on for this item (the server's ``can_resume``), or null. */
export function resumableRun(item: Item | undefined): string | null {
  return item && !item.open && item.can_resume && item.run_id ? item.run_id : null;
}

function Connection({ state, asOf, now }: { state: LiveState; asOf: string | null; now: number | null }) {
  const when = ago(asOf, now);
  const text = state === "live" ? m.board.live : state === "polling" ? m.board.polling : m.board.connecting;
  return (
    <span className="live-state" data-state={state} role="status" aria-live="polite">
      <span className="pdot" aria-hidden="true" />
      {text}
      {when && state !== "polling" ? <span className="mut">{` (${m.board.updated(when)})`}</span> : null}
    </span>
  );
}

function Row({ item, selected, now, onSelect, onStop, onResumed }: {
  item: Item;
  selected: boolean;
  now: number | null;
  onSelect: (id: string) => void;
  onStop: (item: Item) => Promise<void>;
  onResumed: (resumed: ResearchResumed) => void;
}) {
  const resumeRun = resumableRun(item);
  const when = item.open ? ago(item.started_at ?? item.created_at, now) : ago(item.finished_at, now);
  return (
    <li className="trow cols-research" aria-current={selected ? "true" : undefined}>
      <span className="strong">
        {item.company}
        <span className="sub">{item.kind}{item.scope !== "Everything" ? `, ${item.scope.toLowerCase()}` : ""}{item.topic ? `: ${item.topic}` : ""}</span>
      </span>
      <span>
        <span className={`sub-line ${tone(item)}`}>{where(item)}</span>
        {item.open && item.steps_total ? <span className="sub">{m.board.stepsDone(item.steps_done, item.steps_total)}</span> : null}
      </span>
      <span>
        <span className="num">{m.board.counts(item.findings, item.searches)}</span>
        {item.tokens ? <span className="sub">{formatTokens(item.tokens)} {m.board.tokens.toLowerCase()}</span> : null}
      </span>
      <span className="mut sm-text">{when ? (item.open ? m.board.started(when) : m.board.finished(when)) : null}</span>
      <span className="row-12 wrap end-actions">
        <button type="button" className="btn sm" aria-pressed={selected} aria-label={`${selected ? m.board.showing : m.board.show} ${item.company}`} onClick={() => onSelect(item.job_id)}>
          {selected ? m.board.showing : m.board.show}
        </button>
        {item.can_stop ? <StopButton onStop={() => onStop(item)} label={m.board.stop} /> : null}
        {resumeRun ? <ResumeButton runId={resumeRun} compact onResumed={onResumed} /> : null}
      </span>
    </li>
  );
}

function Steps({ detail }: { detail: Detail }) {
  if (!detail.steps.length) return <p className="mut sm-text">{detail.label}</p>;
  return (
    <ol className="steps" aria-label={m.board.steps}>
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

function DetailPanel({ detail, item, now, onResumed }: {
  detail: Detail;
  item: Item | undefined;
  now: number | null;
  onResumed: (resumed: ResearchResumed) => void;
}) {
  const resumeRun = resumableRun(item);
  return (
    <section className="card pad stack-14" aria-labelledby="research-detail-title">
      <div className="row-12 wrap">
        <div className="grow stack-4">
          <h2 id="research-detail-title">{detail.company}</h2>
          <div className="mut sm-text">{detail.label}{detail.reason ? `. ${m.board.reason(detail.reason)}` : ""}</div>
        </div>
        {resumeRun ? <ResumeButton key={resumeRun} runId={resumeRun} compact onResumed={onResumed} /> : null}
        <Link className="btn sm" href={`/accounts/${encodeURIComponent(detail.domain)}`} prefetch={false}>{m.board.openAccount}</Link>
      </div>
      <dl className="kv-grid live-counts">
        <div><dt>{m.board.findings}</dt><dd className="num">{detail.findings}</dd></div>
        <div><dt>{m.board.sources}</dt><dd className="num">{detail.searches}</dd></div>
        <div><dt>{m.board.pages}</dt><dd className="num">{detail.pages_read}</dd></div>
        <div><dt>{m.board.tokens}</dt><dd className="num">{formatTokens(detail.tokens)}</dd></div>
      </dl>
      <div className="live-split">
        <div className="stack-8">
          <h3>{m.board.steps}</h3>
          <Steps detail={detail} />
        </div>
        <div className="stack-8">
          <h3>{m.board.byKind}</h3>
          {detail.findings_by_kind.length ? (
            <ul className="rows">
              {detail.findings_by_kind.map((k) => <li key={k.kind}><span>{k.kind}</span><span className="end num">{k.count}</span></li>)}
            </ul>
          ) : <p className="mut sm-text">{m.board.noFindings}</p>}
        </div>
      </div>
      <div className="stack-8">
        <h3>{m.board.newest}</h3>
        {detail.latest_findings.length ? (
          <ul className="live-findings" aria-live="polite" aria-relevant="additions">
            {detail.latest_findings.map((f) => (
              <li key={f.fact_id} className="lrow">
                <span className="grow stack-4">
                  <span className="mut xs-text">{f.kind}: {f.label}</span>
                  <span>{f.value}</span>
                  {f.source ? <span className="mut xs-text">{m.board.from(f.source)}</span> : null}
                </span>
                <span className="stack-4 align-end">
                  <Grade grade={f.grade} />
                  <span className="when">{ago(f.found_at, now)}</span>
                </span>
              </li>
            ))}
          </ul>
        ) : <p className="mut sm-text">{m.board.noFindings}</p>}
        {detail.withheld ? <p className="fine">{m.board.withheld(detail.withheld)}</p> : null}
      </div>
    </section>
  );
}

export function ResearchBoard({ initial, estimate }: { initial: ResearchLive | null; estimate: ResearchEstimate | null }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const selected = params.get("r");
  const { data, state, restart } = useResearchLive(initial, selected);
  const now = useNow(state === "live" ? 5000 : 15000);
  const [starting, setStarting] = useState(false);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);

  const select = useCallback((id: string) => {
    router.replace(`${pathname}?r=${encodeURIComponent(id)}`, { scroll: false });
  }, [router, pathname]);

  const stop = useCallback(async (item: Item) => {
    setNote(null);
    try {
      await api("POST", `/v1/research/${encodeURIComponent(item.job_id)}/stop`);
      setNote({ text: m.board.stopped });
    } catch (cause) {
      setNote({ text: cause instanceof ApiError && cause.status === 409 ? cause.detail : m.board.stopFailed, bad: true });
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

  if (!data) {
    return <StateCard title={m.board.title} body={m.board.loadFailed} action={<Link className="btn" href="/research" prefetch={false}>{m.run.refresh}</Link>} />;
  }
  const shown = data.detail?.job_id ?? null;
  const shownItem = shown ? [...data.running, ...data.recent].find((i) => i.job_id === shown) : undefined;

  return (
    <div className="stack-22">
      <div className="head">
        <div className="ttl">
          <h1>{m.board.title}</h1>
          <div className="sub">{m.board.sub}</div>
        </div>
        <div className="row-12 wrap">
          <Connection state={state} asOf={data.as_of} now={now} />
          <button type="button" className="btn primary" aria-expanded={starting} onClick={() => setStarting((v) => !v)}>
            {starting ? m.board.hideStart : m.board.start}
          </button>
        </div>
      </div>

      {starting ? (
        <section className="card pad stack-14" aria-labelledby="research-start-title">
          <h2 id="research-start-title">{m.board.startTitle}</h2>
          <ResearchCompany estimate={estimate} idPrefix="rb" onStarted={started} />
        </section>
      ) : null}

      {note ? <p className={`sm-text${note.bad ? " bad-text" : ""}`} role={note.bad ? "alert" : "status"}>{note.text}</p> : null}

      <section className="stack-10" aria-labelledby="research-running-title">
        <h2 id="research-running-title">{m.board.running}</h2>
        {data.running.length ? (
          <div className="card tscroll">
            <div className="trow th cols-research" aria-hidden="true">
              <span>{m.board.company}</span><span>{m.board.progress}</span><span>{m.board.found}</span><span /><span />
            </div>
            <ul aria-label={m.board.running}>
              {data.running.map((item) => (
                <Row key={item.job_id} item={item} selected={item.job_id === shown} now={now} onSelect={select} onStop={stop} onResumed={resumed} />
              ))}
            </ul>
          </div>
        ) : <p className="mut sm-text">{m.board.noneRunning}</p>}
      </section>

      {data.detail ? <DetailPanel detail={data.detail} item={shownItem} now={now} onResumed={resumed} /> : null}

      <section className="stack-10" aria-labelledby="research-recent-title">
        <h2 id="research-recent-title">{m.board.recent}</h2>
        {data.recent.length ? (
          <div className="card tscroll">
            <ul aria-label={m.board.recent}>
              {data.recent.map((item) => (
                <Row key={item.job_id} item={item} selected={item.job_id === shown} now={now} onSelect={select} onStop={stop} onResumed={resumed} />
              ))}
            </ul>
          </div>
        ) : <p className="mut sm-text">{m.board.noneRecent}</p>}
      </section>
    </div>
  );
}
