"use client";
// Research a new company on the credit pool (Add account section 1 and New research, company
// tab): Company name and Website, then Start research shows the credit estimate and the credits
// left; nothing is reserved or searched until Confirm. Confirm sends the credits the user saw, so
// a moved estimate is shown again instead of being charged. The account cap and a short pool
// disable Confirm with the reason.
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { DOMAIN_RE, cleanDomain } from "@/lib/documents";
import type { ResearchEstimate, ResearchStarted } from "@/lib/gen/api.gen";

function duration(minutes: number): string {
  if (minutes >= 240) return "about a working day";
  if (minutes >= 60) return `about ${Math.round(minutes / 60)} hours`;
  return `about ${minutes} minutes`;
}

function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

export function ResearchCompany({ estimate, idPrefix = "rc" }: { estimate: ResearchEstimate | null; idPrefix?: string }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [site, setSite] = useState("");
  const [asking, setAsking] = useState(false);
  const [credits, setCredits] = useState(estimate?.credits ?? 0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const requestId = useRef(crypto.randomUUID());
  const domain = cleanDomain(site);
  const valid = name.trim().length > 0 && DOMAIN_RE.test(domain);
  const atCap = estimate !== null && estimate.accounts_used >= estimate.account_cap;
  const left = estimate?.pool.left ?? 0;
  const short = credits > left;

  if (!estimate) {
    return <p className="mut sm-text">Research is unavailable right now. Reload the page in a moment.</p>;
  }

  const confirm = async () => {
    if (busy || short || atCap || !valid) return;
    setBusy(true);
    setMessage(null);
    try {
      await api<ResearchStarted>("POST", "/v1/research", {
        request_id: requestId.current,
        company_name: name.trim(),
        domain,
        credits,
      });
      router.push("/accounts");
      router.refresh();
    } catch (cause) {
      requestId.current = crypto.randomUUID();
      if (cause instanceof ApiError && cause.type === "estimate_changed" && typeof cause.context?.credits === "number") {
        setCredits(cause.context.credits as number);
        setMessage(cause.detail);
      } else if (cause instanceof ApiError && cause.status === 409) {
        setMessage(cause.detail);
      } else if (cause instanceof ApiError && cause.status === 422) {
        setMessage("Check the company name and website.");
      } else {
        setMessage("The research could not be started. Try again in a moment.");
      }
      setBusy(false);
    }
  };

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
      <p className="mut sm-text">Researched from public sources, using credits from your pool. Takes about a working day. You are told when it is ready.</p>
      {atCap ? (
        <p className="mut sm-text" role="status">You have used all {estimate.account_cap} accounts. Ask your administrator to raise the cap.</p>
      ) : null}
      {asking ? (
        <div className="card wash pad-sm stack-10" role="group" aria-label="Confirm the research">
          <p className="sm-text">
            Researching <strong>{name.trim()}</strong> uses about <strong>{fmt(credits)} credits</strong> and takes {duration(estimate.minutes)}.
            {" "}Research credits left this month: {fmt(left)} of {fmt(estimate.pool.pool)}.
          </p>
          {short ? <p className="sm-text bad-text" role="status">Not enough credits left this month for this research.</p> : null}
          {message ? <p className="sm-text" role="alert">{message}</p> : null}
          <div className="row-12">
            <button type="button" className="btn primary" disabled={busy || short || atCap} onClick={() => void confirm()}>
              {busy ? "Starting…" : "Confirm research"}
            </button>
            <button type="button" className="btn" onClick={() => { setAsking(false); setMessage(null); }}>Cancel</button>
          </div>
        </div>
      ) : (
        <button type="button" className="btn align-start" disabled={!valid || atCap} onClick={() => setAsking(true)}>Start research</button>
      )}
    </div>
  );
}
