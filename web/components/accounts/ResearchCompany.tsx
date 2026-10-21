"use client";
// Research a new company (Add account section 1 and New research, company tab): Company name and
// Website, the two model chips, then Start research shows what it uses ("1 of your 2 researches
// left this month") and nothing starts until Confirm. With no research left, the refusal and
// "Request more from admin" show instead (owner decision 2026-10-14: researches, not credits).
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { LimitNote, LimitReached, limitLine, limitReached, useMyLimits } from "@/components/Limits";
import { ResearchModelChips, useResearchModels } from "@/components/ModelMenu";
import { api, ApiError } from "@/lib/api";
import { DOMAIN_RE, cleanDomain } from "@/lib/documents";
import type { ResearchEstimate, ResearchStarted } from "@/lib/gen/api.gen";

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
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [hit, setHit] = useState<string | null>(null);
  const requestId = useRef(crypto.randomUUID());
  const models = useResearchModels();
  const { limits } = useMyLimits();
  const domain = cleanDomain(site);
  const valid = name.trim().length > 0 && DOMAIN_RE.test(domain);
  const none = limits !== null && limits.researches.left === 0;

  if (!estimate) {
    return <p className="mut sm-text">Research is unavailable right now. Reload the page in a moment.</p>;
  }

  const confirm = async () => {
    if (busy || !valid) return;
    setBusy(true);
    setMessage(null);
    try {
      const started = await api<ResearchStarted>("POST", "/v1/research", {
        request_id: requestId.current,
        company_name: name.trim(),
        domain,
        ...models.body,
      });
      if (onStarted) {
        // the Research screen follows it live where it is
        requestId.current = crypto.randomUUID();
        setName("");
        setSite("");
        setAsking(false);
        setBusy(false);
        onStarted(started);
        return;
      }
      // the account page shows the research live: its steps, Stop and Refresh
      router.push(`/accounts/${encodeURIComponent(started.domain || domain)}`);
      router.refresh();
    } catch (cause) {
      requestId.current = crypto.randomUUID();
      const reached = limitReached(cause);
      if (reached) setHit(reached.message);
      else if (cause instanceof ApiError && cause.status === 409) setMessage(cause.detail);
      else if (cause instanceof ApiError && cause.status === 422) setMessage(cause.detail || "Check the company name and website.");
      else setMessage("The research could not be started. Try again in a moment.");
      setBusy(false);
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
      <ResearchModelChips state={models} />
      <p className="mut sm-text">Researched from public sources. Takes {duration(estimate.minutes)}. You are told when it is ready.</p>
      {refusal ? (
        <LimitReached kind="researches" message={refusal} pending={limits?.pending_requests.includes("researches")} />
      ) : asking ? (
        <div className="card wash pad-sm stack-10" role="group" aria-label="Confirm the research">
          <p className="sm-text">
            Researching <strong>{name.trim()}</strong> uses one research{limits ? ` (${limitLine(limits, "researches")})` : ""}.
          </p>
          {message ? <p className="sm-text" role="alert">{message}</p> : null}
          <div className="row-12">
            <button type="button" className="btn primary" disabled={busy} onClick={() => void confirm()}>
              {busy ? "Starting…" : "Confirm research"}
            </button>
            <button type="button" className="btn" onClick={() => { setAsking(false); setMessage(null); }}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="stack-8">
          <button type="button" className="btn primary align-start" disabled={!valid} onClick={() => setAsking(true)}>Start research</button>
          <LimitNote limits={limits} kind="researches" />
        </div>
      )}
    </div>
  );
}
