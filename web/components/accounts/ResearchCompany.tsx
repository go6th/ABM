"use client";
// Research a new company (Add account section 1 and New research, company tab): Company name and
// Website, the two model chips, then Start research shows what it uses ("1 of your 2 researches
// left this month") with the tokens and dollars it is expected to use ("Proceed?", owner decision
// 2026-10-21), and nothing starts until Proceed. With no research left, the refusal and "Request
// more from admin" show instead (owner decision 2026-10-14: researches, not credits).
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { LimitNote, LimitReached, limitLine, limitReached, useMyLimits } from "@/components/Limits";
import { ResearchModelChips, useResearchModels } from "@/components/ModelMenu";
import { UsageProceed } from "@/components/UsageProceed";
import { api, ApiError } from "@/lib/api";
import { DOMAIN_RE, cleanDomain } from "@/lib/documents";
import type { ResearchEstimate, ResearchStarted } from "@/lib/gen/api.gen";
import { reasonOr, useRequestKey } from "./ResumeResearch";

// the research goal is optional; the API takes 3 to 500 characters once whitespace is collapsed
const GOAL_MAX = 500;

function duration(minutes: number): string {
  if (minutes >= 240) return "about a working day";
  if (minutes >= 60) return `about ${Math.round(minutes / 60)} hours`;
  return `about ${minutes} minutes`;
}

export function ResearchCompany({ estimate, idPrefix = "rc", onStarted }: {
  estimate: ResearchEstimate | null;
  idPrefix?: string;
  /** Called with the started research instead of opening its account (the Research screen). */
  onStarted?: (started: ResearchStarted) => void;
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [site, setSite] = useState("");
  const [goal, setGoal] = useState("");
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [hit, setHit] = useState<string | null>(null);
  const request = useRequestKey();
  // a second Proceed before the first re-render must not post again
  const inFlight = useRef(false);
  const models = useResearchModels();
  const { limits } = useMyLimits();
  const domain = cleanDomain(site);
  const goalOk = goal.trim().length === 0 || goal.trim().length >= 3;
  const valid = name.trim().length > 0 && DOMAIN_RE.test(domain) && goalOk;
  const none = limits !== null && limits.researches.left === 0;

  if (!estimate) {
    return <p className="mut sm-text">Research is unavailable right now. Reload the page in a moment.</p>;
  }

  const confirm = async () => {
    if (inFlight.current || busy || !valid) return;
    inFlight.current = true;
    setBusy(true);
    setMessage(null);
    const body = {
      company_name: name.trim(),
      domain,
      ...(goal.trim() ? { goal: goal.trim() } : {}),
      ...models.body,
    };
    try {
      const started = await api<ResearchStarted>("POST", "/v1/research", { request_id: request.key(body), ...body });
      request.done();
      if (onStarted) {
        // the Research screen follows it live where it is
        setName("");
        setSite("");
        setGoal("");
        setAsking(false);
        setBusy(false);
        onStarted(started);
        return;
      }
      // the account page shows the research live: its steps, Stop and Refresh
      router.push(`/accounts/${encodeURIComponent(started.domain || domain)}`);
      router.refresh();
    } catch (cause) {
      // a network error or a 5xx keeps the request id, so trying again cannot start it twice
      request.failed(cause);
      const reached = limitReached(cause);
      if (reached) setHit(reached.message);
      else if (cause instanceof ApiError && cause.status === 409) setMessage(reasonOr(cause, "This company is already being researched."));
      else if (cause instanceof ApiError && cause.status === 422) setMessage(reasonOr(cause, "Check the company name and website."));
      else setMessage("The research could not be started. Try again in a moment.");
      setBusy(false);
    } finally {
      inFlight.current = false;
    }
  };

  const refusal = hit ?? (none && limits
    ? `You have used your ${limits.researches.limit} ${limits.researches.limit === 1 ? "research" : "researches"}. Ask your admin to increase your limit.`
    : null);

  return (
    <div className="stack-14">
      <div className="row-12 wrap">
        <div className="field grow-220">
          <label htmlFor={`${idPrefix}-name`}>Company name</label>
          <input id={`${idPrefix}-name`} className="f" placeholder="Company name" value={name} maxLength={200}
            onChange={(e) => { setName(e.target.value); setAsking(false); }} />
        </div>
        <div className="field grow-220">
          <label htmlFor={`${idPrefix}-site`}>Website</label>
          <input id={`${idPrefix}-site`} className="f" placeholder="company.com" value={site} maxLength={253}
            aria-invalid={site.length > 0 && !DOMAIN_RE.test(domain)}
            onChange={(e) => { setSite(e.target.value); setAsking(false); }} />
        </div>
      </div>
      <div className="field">
        <label htmlFor={`${idPrefix}-goal`}>Research goal (optional)</label>
        <textarea id={`${idPrefix}-goal`} className="textarea" rows={2} maxLength={GOAL_MAX} value={goal}
          placeholder="For example: we sell payroll software, so focus on their HR systems and hiring"
          aria-describedby={`${idPrefix}-goal-hint`}
          onChange={(e) => { setGoal(e.target.value); setAsking(false); }} />
        <div className="topic-hint mut" id={`${idPrefix}-goal-hint`}>
          <span>{goal.trim().length > 0 && goal.trim().length < 3 ? "Describe the goal in a few words." : "Adds a few searches and leads the summary with what matters to you."}</span>
          <span className="count">{goal.length}/{GOAL_MAX}</span>
        </div>
      </div>
      <ResearchModelChips state={models} />
      <p className="mut sm-text">Researched from public sources. Takes {duration(estimate.minutes)}. You are told when it is ready.</p>
      {refusal ? (
        <LimitReached kind="researches" message={refusal} pending={limits?.pending_requests.includes("researches")} />
      ) : asking ? (
        <UsageProceed
          usage={estimate.usage}
          what={`Researching ${name.trim()}`}
          busy={busy}
          message={message}
          note={<p className="mut xs-text">It uses one research{limits ? ` (${limitLine(limits, "researches")})` : ""}.</p>}
          onProceed={() => void confirm()}
          onCancel={() => { setAsking(false); setMessage(null); }}
        />
      ) : (
        <div className="stack-8">
          <button type="button" className="btn primary align-start" disabled={!valid} onClick={() => setAsking(true)}>Start research</button>
          <LimitNote limits={limits} kind="researches" />
        </div>
      )}
    </div>
  );
}
