"use client";
// New research as designed: Company name | Upload a file tabs. The company tab is the shared
// ResearchCompany form (credits shown and confirmed first). The file tab: the drop area and the
// file list (Scanning, Ready, Failed), "We found more than one company. Which is this about?" when
// the files name several, the Also check public sources switch (off by default), the Estimated
// cost (0 research credits, or about N credits confirmed before it runs) and Analyze file. Results
// open on the account's file analysis screen.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { DOMAIN_RE, allIndexed, cleanDomain, detectedCompany, needingCompany, type CompanyCandidate } from "@/lib/documents";
import type { CompanyConfirmed, DocumentEstimate, DocumentStatus, ResearchEstimate, RunCreated, RunStatusView } from "@/lib/gen/api.gen";
import { DocumentDrop } from "@/components/ws/DocumentDrop";
import { ResearchCompany } from "./ResearchCompany";

function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

function startError(error: unknown): string {
  if (!(error instanceof ApiError)) return "The analysis could not be started. Try again.";
  if (error.type === "documents_not_ready") return "Some files are still being read. Wait until every file shows Ready.";
  if (error.type === "company_confirmation_required") return "Choose which company these files are about first.";
  if (error.type === "insufficient_credits" || error.status === 402) return "Not enough research credits left this month to check public sources.";
  if (error.status === 409) return error.detail || "An analysis of these files is already starting.";
  return error.detail || "The analysis could not be started. Try again.";
}

function CompanyChoice({ docs, onConfirmed }: { docs: DocumentStatus[]; onConfirmed: (accountId: string) => void }) {
  const detected = detectedCompany(docs);
  const options: CompanyCandidate[] = detected.candidates.length ? detected.candidates : detected.company ? [detected.company] : [];
  const [pick, setPick] = useState<number>(0);
  const [name, setName] = useState(options[0]?.name ?? "");
  const [site, setSite] = useState(options[0]?.domain ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const domain = cleanDomain(site);
  const choose = (i: number) => {
    setPick(i);
    setName(options[i]?.name ?? "");
    setSite(options[i]?.domain ?? "");
  };
  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const done = await api<CompanyConfirmed>("POST", "/v1/documents/confirm-company", { document_ids: docs.map((d) => d.document_id), company_name: name.trim(), domain });
      onConfirmed(done.account_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "The company could not be confirmed. Try again.");
      setBusy(false);
    }
  };
  return (
    <section className="card pad stack-12" aria-labelledby="which-company">
      <div className="strong" id="which-company">
        {options.length > 1 ? "We found more than one company. Which is this about?" : options.length === 1 ? "Is this about this company?" : "Which company is this about?"}
      </div>
      {options.length ? (
        <div className="stack-10" role="radiogroup" aria-label="Company">
          {options.map((c, i) => (
            <button key={`${c.domain}-${c.name}`} type="button" className="opt" role="radio" aria-checked={pick === i} onClick={() => choose(i)}>
              <span className="in">
                <span className="radio-dot" aria-hidden="true" />
                <span className="grow strong">{c.name ?? c.domain}</span>
                <span className="mut xs-text">{c.mentions ? `Mentioned ${c.mentions} times` : c.domain ?? ""}</span>
              </span>
            </button>
          ))}
        </div>
      ) : null}
      <div className="row-12 wrap">
        <div className="field grow-220">
          <label htmlFor="fc-name">Company name</label>
          <input id="fc-name" className="f" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field grow-220">
          <label htmlFor="fc-site">Website</label>
          <input id="fc-site" className="f" value={site} maxLength={253} placeholder="company.com" aria-invalid={site.length > 0 && !DOMAIN_RE.test(domain)} onChange={(e) => setSite(e.target.value)} />
        </div>
      </div>
      {error ? <p className="sm-text bad-text" role="alert">{error}</p> : null}
      <button type="button" className="btn align-start" disabled={busy || !name.trim() || !DOMAIN_RE.test(domain)} onClick={() => void confirm()}>
        {busy ? "Confirming…" : "This is the company"}
      </button>
    </section>
  );
}

function FileTab() {
  const router = useRouter();
  const [docs, setDocs] = useState<DocumentStatus[]>([]);
  const [accountId, setAccountId] = useState<string | null>(null);
  const [enrich, setEnrich] = useState(false);
  const [estimate, setEstimate] = useState<DocumentEstimate | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const onChange = useCallback((next: DocumentStatus[]) => setDocs(next), []);
  const waiting = needingCompany(docs);
  const ready = allIndexed(docs);
  const bound = accountId ?? docs.find((d) => d.account_id)?.account_id ?? null;
  const needsChoice = waiting.length > 0 && docs.every((d) => d.stage === "parsed" || d.stage === "indexed" || d.stage === "failed");

  useEffect(() => {
    if (!ready) return;
    let live = true;
    api<DocumentEstimate>("POST", "/v1/documents/estimate", { document_ids: docs.map((d) => d.document_id), enrich_public: enrich, credit_cap: null })
      .then((e) => { if (live) setEstimate(e); })
      .catch(() => { if (live) setEstimate(null); });
    return () => { live = false; };
  }, [ready, docs, enrich]);

  const credits = enrich ? estimate?.credits ?? null : 0;
  const short = enrich && estimate !== null && estimate.credits > estimate.credits_left;
  const cost = !enrich ? "0 research credits" : credits === null ? "About … credits, confirmed before it runs" : `About ${fmt(credits)} credits, confirmed before it runs`;

  const start = async () => {
    setBusy(true);
    setError(null);
    key.current ??= newIdempotencyKey();
    try {
      const created = await api<RunCreated>("POST", "/v1/runs", {
        input_mode: "file", file: { document_ids: docs.map((d) => d.document_id), account_id: bound, enrich_public: enrich, credit_cap: null },
      }, { "Idempotency-Key": key.current });
      const run = await api<RunStatusView>("GET", `/v1/runs/${encodeURIComponent(created.run_id)}`);
      router.push(run.domain
        ? `/accounts/${encodeURIComponent(run.domain)}/files?run=${encodeURIComponent(created.run_id)}`
        : "/accounts");
    } catch (e) {
      setError(startError(e));
      key.current = null;
      setBusy(false);
    }
  };

  const analyze = () => {
    if (!ready || busy) return;
    if (enrich && !asking) {
      setAsking(true);
      return;
    }
    void start();
  };

  return (
    <div className="stack-20">
      <DocumentDrop onChange={onChange} disabled={busy} />
      {needsChoice ? <CompanyChoice docs={waiting} onConfirmed={setAccountId} /> : null}
      {docs.length ? (
        <>
          <section className="card pad row-16 wrap">
            <div className="grow-280">
              <div className="strong">Also check public sources</div>
              <p className="mut sm-text mt-2">Off by default. File-only analysis uses no research credits and stays inside your workspace.</p>
            </div>
            <button type="button" className="sw" role="switch" aria-checked={enrich} aria-label="Also check public sources"
              onClick={() => { setEnrich((v) => !v); setAsking(false); }} />
          </section>
          <div className="card pad-sm row-space wrap">
            <span className="mut">Estimated cost</span>
            <b>{cost}</b>
          </div>
          {asking ? (
            <div className="card wash pad-sm stack-10" role="group" aria-label="Confirm public sources">
              <p className="sm-text">
                Checking public sources uses about <strong>{fmt(credits ?? 0)} credits</strong>.
                {estimate ? ` Research credits left this month: ${fmt(estimate.credits_left)}.` : ""}
              </p>
              {short ? <p className="sm-text bad-text" role="status">Not enough credits left this month. Switch public sources off to analyze the files alone.</p> : null}
            </div>
          ) : null}
          {error ? <p className="sm-text bad-text" role="alert">{error}</p> : null}
          <div className="row-12 wrap">
            <button type="button" className="btn" disabled={!ready || busy || short || !bound} onClick={analyze}>
              {busy ? "Starting…" : asking ? "Confirm and analyze" : "Analyze file"}
            </button>
            <span className="mut sm-text">Results are marked <b>From your files</b> and kept private to your workspace.</span>
          </div>
        </>
      ) : null}
    </div>
  );
}

export function NewResearch({ estimate, initialTab }: { estimate: ResearchEstimate | null; initialTab: "company" | "file" }) {
  const [tab, setTab] = useState<"company" | "file">(initialTab);
  return (
    <>
      <div className="seg tabs" role="tablist" aria-label="Research input">
        <button type="button" role="tab" aria-selected={tab === "company"} onClick={() => setTab("company")}>Company name</button>
        <button type="button" role="tab" aria-selected={tab === "file"} onClick={() => setTab("file")}>Upload a file</button>
      </div>
      {tab === "company" ? (
        <section className="card pad-lg stack-14" aria-label="Research a company">
          <ResearchCompany estimate={estimate} idPrefix="nr" />
        </section>
      ) : (
        <FileTab />
      )}
    </>
  );
}
