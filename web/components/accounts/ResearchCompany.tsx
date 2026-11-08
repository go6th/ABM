"use client";
// Research a new company (Add account section 1 and New research, company tab): Company name and
// Website, an optional goal, the model chips, then the cost with Start research (UsageProceed:
// the first press arms it, the second starts; owner decisions 2026-10-21 and 2026-11-05). With no
// research left, the refusal and "Request more from admin" show instead (owner decision
// 2026-10-14: researches, not credits).
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { LimitReached, limitLine, limitReached, useMyLimits } from "@/components/Limits";
import { ResearchModelChips, useResearchModels } from "@/components/ModelMenu";
import { CONFIRMED_TWICE, UsageProceed } from "@/components/UsageProceed";
import { api, ApiError } from "@/lib/api";
import { DOMAIN_RE, cleanDomain } from "@/lib/documents";
import type { ResearchEstimate, ResearchStarted } from "@/lib/gen/api.gen";
import { useRequestKey } from "./ResumeResearch";
import { errorText } from "@/lib/api";
import { announce } from "@/ui/Toaster";

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
    return <p className="mut sm-text">Research is unavailable right now. <a className="link" href="">Reload</a> to try again.</p>;
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
      ...CONFIRMED_TWICE,
    };
    try {
      const started = await api<ResearchStarted>("POST", "/v1/research", { request_id: request.key(body), ...body });
      request.done();
      if (onStarted) {
        // the Research screen follows it live where it is
        setName("");
        setSite("");
        setGoal("");
        setBusy(false);
        announce(`Research on ${body.company_name} started. You will be notified when it is ready.`);
        onStarted(started);
        return;
      }
      // the account page shows the research live: its steps, Stop and Refresh
      announce(`Research on ${body.company_name} started. You will be notified when it is ready.`);
      router.push(`/accounts/${encodeURIComponent(started.domain || domain)}`);
      router.refresh();
    } catch (cause) {
      // a network error or a 5xx keeps the request id, so trying again cannot start it twice
      request.failed(cause);
      const reached = limitReached(cause);
      if (reached) setHit(reached.message);
      else if (cause instanceof ApiError && cause.status === 409) setMessage(errorText(cause, "This company is already being researched."));
      else if (cause instanceof ApiError && cause.status === 422) setMessage(errorText(cause, "Check the company name and website."));
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
            onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field grow-220">
          <label htmlFor={`${idPrefix}-site`}>Website</label>
          <input id={`${idPrefix}-site`} className="f" placeholder="company.com" value={site} maxLength={253}
            aria-invalid={site.length > 0 && !DOMAIN_RE.test(domain)}
            aria-describedby={site.length > 0 && !DOMAIN_RE.test(domain) ? `${idPrefix}-site-error` : undefined}
            onChange={(e) => setSite(e.target.value)} />
          {site.length > 0 && !DOMAIN_RE.test(domain) ? <p className="field-error" id={`${idPrefix}-site-error`}>Enter the website as company.com.</p> : null}
        </div>
      </div>
      <div className="field">
        <label htmlFor={`${idPrefix}-goal`}>Research goal (optional)</label>
        <textarea id={`${idPrefix}-goal`} className="textarea" rows={2} maxLength={GOAL_MAX} value={goal}
          placeholder="For example: we sell payroll software, so focus on their HR systems and hiring"
          aria-describedby={`${idPrefix}-goal-hint`}
          onChange={(e) => setGoal(e.target.value)} />
        <div className="topic-hint mut" id={`${idPrefix}-goal-hint`}>
          <span>{goal.trim().length > 0 && goal.trim().length < 3 ? "Describe the goal in a few words." : "Adds a few searches and leads the summary with what matters to you."}</span>
          <span className="count">{goal.length}/{GOAL_MAX}</span>
        </div>
      </div>
      <ResearchModelChips state={models} />
      {refusal ? (
        <LimitReached kind="researches" message={refusal} pending={limits?.pending_requests.includes("researches")} />
      ) : (
        <UsageProceed
          key={`${name.trim()}:${domain}:${goal.trim()}:${JSON.stringify(models.body)}`}
          usage={estimate.usage}
          gaps={estimate.gaps}
          what={name.trim() ? `Researching ${name.trim()}` : "This research"}
          label="Start research"
          busy={busy}
          disabled={!valid}
          reason={!name.trim() || !domain ? "Enter the company name and website first." : "Check the website and goal first."}
          message={message}
          note={<p className="mut xs-text">Researched from public sources; takes {duration(estimate.minutes)} and uses one research{limits ? ` (${limitLine(limits, "researches")})` : ""}. You are told when it is ready.</p>}
          onProceed={() => void confirm()}
          onCancel={() => setMessage(null)}
        />
      )}
    </div>
  );
}
