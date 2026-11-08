"use client";
// Import a file (Accounts, 2026-11-08: the owner keeps adding and importing accounts on Accounts,
// so the upload left the Research page): "Add files", the file list
// (Uploading, Scanning, Reading, Ready, Failed). When the files name one company they are attached
// to it (its account is created when needed; owner decision 2026-10-14), with "Change"; otherwise
// "Which company is this file for?" with just a company name and website (no list of detected
// companies; owner decision 2026-11-06), prefilled with the one the files name most. The Also
// check public sources switch (off by default), what it uses (a file analysis is one chat; with
// public sources also one research; owner decision 2026-10-14) and Analyze file, which asks "this
// analysis will use about N tokens (about $X). Proceed?" first (owner decision 2026-10-21).
// Results open on the account's file analysis screen.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { DOMAIN_RE, allIndexed, cleanDomain, detectedCompany, isSettled, needingCompany, type CompanyCandidate } from "@/lib/documents";
import type { CompanyConfirmed, DocumentStatus, ResearchEstimate, RunCreated, RunStatusView } from "@/lib/gen/api.gen";
import { LimitNote, LimitReached, limitReached, useMyLimits } from "@/components/Limits";
import { CONFIRMED_TWICE, UsageProceed } from "@/components/UsageProceed";
import { DocumentDrop } from "@/components/ws/DocumentDrop";
import { errorText } from "@/lib/api";

function startError(error: unknown): string {
  if (!(error instanceof ApiError)) return "The analysis could not be started. Try again.";
  if (error.type === "documents_not_ready") return "Some files are still being read. Wait until every file shows Ready.";
  if (error.type === "company_confirmation_required") return "Choose which company these files are about first.";
  if (error.status === 409) return errorText(error, "An analysis of these files is already starting.");
  return errorText(error, "The analysis could not be started. Try again.");
}

function CompanyChoice({ docs, onConfirmed }: { docs: DocumentStatus[]; onConfirmed: (accountId: string, company: Company) => void }) {
  const detected = detectedCompany(docs);
  // prefilled with the company the files name most; the user can edit either field
  const first: CompanyCandidate | null = detected.candidates[0] ?? detected.company ?? null;
  const [name, setName] = useState(first?.name ?? "");
  const [site, setSite] = useState(cleanDomain(first?.domain ?? ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const domain = cleanDomain(site);
  const inFlight = useRef(false);
  const confirm = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const done = await api<CompanyConfirmed>("POST", "/v1/documents/confirm-company", { document_ids: docs.map((d) => d.document_id), company_name: name.trim(), domain });
      onConfirmed(done.account_id, { name: name.trim(), domain });
    } catch (e) {
      setError(errorText(e, "The company could not be confirmed. Try again."));
      setBusy(false);
    } finally {
      inFlight.current = false;
    }
  };
  return (
    <section className="card pad stack-12" aria-labelledby="which-company">
      <div className="strong" id="which-company">Which company is this file for?</div>
      <p className="mut sm-text">Add the company name and website, then continue. Nothing is attached until you do.</p>
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
        {busy ? "Confirming…" : "Continue"}
      </button>
    </section>
  );
}

/** Attach the files to the one company they name (creating its account when needed). */
interface Company { name: string; domain: string }

/** Files that finish reading after the company was confirmed join the same company, so no file
 * is left at "waiting for the company" once the choice has closed (2026-10-15). */
export function useAttachLate(waiting: DocumentStatus[], company: Company | null, accountId: string | null, onDone: () => void) {
  const sent = useRef(new Set<string>());
  useEffect(() => {
    if (!company || !accountId) return;
    const late = waiting.filter((d) => isSettled(d.stage) && !sent.current.has(d.document_id));
    if (!late.length) return;
    late.forEach((d) => sent.current.add(d.document_id));
    api<CompanyConfirmed>("POST", "/v1/documents/confirm-company", { document_ids: late.map((d) => d.document_id), company_name: company.name, domain: company.domain })
      .then(onDone)
      // a refusal (4xx) is final for these files: sending the same request every poll only
      // repeats it; a network error or a 5xx is retried on the next read
      .catch((e: unknown) => { if (!(e instanceof ApiError) || e.status >= 500) late.forEach((d) => sent.current.delete(d.document_id)); });
  }, [waiting, company, accountId, onDone]);
}

export function ImportFile({ estimate }: { estimate: ResearchEstimate | null }) {
  const router = useRouter();
  const [docs, setDocs] = useState<DocumentStatus[]>([]);
  const [accountId, setAccountId] = useState<string | null>(null);
  const [attached, setAttached] = useState<string | null>(null);
  const [company, setCompany] = useState<Company | null>(null);
  const [changing, setChanging] = useState(false);
  const [enrich, setEnrich] = useState(false);
  const [hit, setHit] = useState<{ kind: "researches" | "chats"; message: string } | null>(null);
  const { limits } = useMyLimits();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // the Idempotency-Key of one analysis: kept while the same files and switch are retried after a
  // network error or a 5xx (the server may have started it), new after a refusal or a change
  const key = useRef<{ value: string; body: string } | null>(null);
  const inFlight = useRef(false);
  const [refresh, setRefresh] = useState(0);
  const onChange = useCallback((next: DocumentStatus[]) => setDocs(next), []);
  const waiting = needingCompany(docs);
  const ready = allIndexed(docs);
  const bound = accountId ?? docs.find((d) => d.account_id)?.account_id ?? null;
  const settled = waiting.length > 0 && docs.every((d) => isSettled(d.stage) || d.stage === "failed");
  const reread = useCallback(() => setRefresh((n) => n + 1), []);
  useAttachLate(waiting, company, accountId, reread);
  // the files are never attached by themselves (2026-11-05, owner): once they are read the page
  // asks which account they are for and its link, prefilled from what the files name, and waits
  // for Continue. Once a company is confirmed the choice closes, even before the files report
  // "indexed"
  const needsChoice = (!accountId && settled) || changing;

  const uses = enrich ? "One chat and one research" : "One chat";

  const start = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    const body = { input_mode: "file", file: { document_ids: docs.map((d) => d.document_id), account_id: bound, enrich_public: enrich, credit_cap: null }, ...CONFIRMED_TWICE };
    const text = JSON.stringify(body);
    if (key.current?.body !== text) key.current = { value: newIdempotencyKey(), body: text };
    let created: RunCreated;
    try {
      created = await api<RunCreated>("POST", "/v1/runs", body, { "Idempotency-Key": key.current.value });
    } catch (e) {
      const reached = limitReached(e);
      if (reached) setHit(reached);
      else setError(startError(e));
      if (e instanceof ApiError && e.status < 500) key.current = null;
      setBusy(false);
      inFlight.current = false;
      return;
    }
    key.current = null;
    // the analysis has started: a failed read of its account opens the run itself instead of
    // saying it could not start (a retry would start a second one)
    let domain: string | null = null;
    try {
      domain = (await api<RunStatusView>("GET", `/v1/runs/${encodeURIComponent(created.run_id)}`)).domain ?? null;
    } catch {
      domain = null;
    }
    router.push(domain
      ? `/accounts/${encodeURIComponent(domain)}/file-analysis?run=${encodeURIComponent(created.run_id)}`
      : `/runs/${encodeURIComponent(created.run_id)}`);
  };

  const usage = estimate ? (enrich ? estimate.file_public_usage : estimate.file_usage) : null;

  return (
    <div className="stack-20">
      <DocumentDrop picker={false} onChange={onChange} disabled={busy} refresh={refresh} />
      {attached && !changing ? (
        <div className="card pad-sm row-space wrap" role="status">
          <span>These files are about <b>{attached}</b>.</span>
          <button type="button" className="link-btn small" onClick={() => setChanging(true)}>Change</button>
        </div>
      ) : null}
      {needsChoice ? (
        <CompanyChoice
          docs={changing ? docs.filter((d) => isSettled(d.stage)) : waiting}
          onConfirmed={(id, chosen) => { setAccountId(id); setCompany(chosen); setAttached(chosen.name); setChanging(false); setRefresh((n) => n + 1); }}
        />
      ) : null}
      {docs.length ? (
        <>
          <section className="card pad row-16 wrap">
            <div className="grow-280">
              <div className="strong">Also check public sources</div>
              <p className="mut sm-text mt-2">Off by default. Your files stay inside your workspace either way; checking public sources also uses one research.</p>
            </div>
            <button type="button" className="sw" role="switch" aria-checked={enrich} aria-label="Also check public sources"
              onClick={() => setEnrich((v) => !v)} />
          </section>
          {hit ? <LimitReached kind={hit.kind} message={hit.message} pending={limits?.pending_requests.includes(hit.kind)} /> : null}
          <LimitNote limits={limits} kind="chats" />
          {hit === null ? (
            <UsageProceed
              key={`${enrich}:${docs.map((d) => d.document_id).join(",")}`}
              usage={usage}
              what="This analysis"
              doing={enrich ? "starts the model reading your files and research on the web" : "starts the model reading your files"}
              label="Analyze file"
              busy={busy}
              disabled={!ready || !bound}
              reason={!bound ? "Choose which company these files are about first." : "Wait until every file shows Ready."}
              message={error}
              note={<p className="mut xs-text">Uses {uses.toLowerCase()}. Results are marked From your files and stay private to your workspace.{enrich ? " Checking public sources also researches the company on the web." : ""}</p>}
              onProceed={() => void start()}
              onCancel={() => setError(null)}
            />
          ) : null}
        </>
      ) : null}
    </div>
  );
}
