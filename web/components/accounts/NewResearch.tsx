"use client";
// New research as designed: Company name | Upload a file tabs. The company tab is the shared
// ResearchCompany form. The file tab: "Add files" or "Connect Google Drive", the file list
// (Uploading, Scanning, Reading, Ready, Failed). When the files name one company they are attached
// to it (its account is created when needed; owner decision 2026-10-14), with "Change"; "We found
// more than one company. Which is this about?" when the files name several. The Also
// check public sources switch (off by default), what it uses (a file analysis is one chat; with
// public sources also one research; owner decision 2026-10-14) and Analyze file, which asks "this
// analysis will use about N tokens (about $X). Proceed?" first (owner decision 2026-10-21).
// Results open on the account's file analysis screen.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { DOMAIN_RE, allIndexed, cleanDomain, detectedCompany, needingCompany, type CompanyCandidate } from "@/lib/documents";
import type { CompanyConfirmed, DocumentStatus, ResearchEstimate, RunCreated, RunStatusView } from "@/lib/gen/api.gen";
import { LimitNote, LimitReached, limitReached, useMyLimits } from "@/components/Limits";
import { UsageProceed } from "@/components/UsageProceed";
import { DocumentDrop } from "@/components/ws/DocumentDrop";
import { ResearchCompany } from "./ResearchCompany";

function startError(error: unknown): string {
  if (!(error instanceof ApiError)) return "The analysis could not be started. Try again.";
  if (error.type === "documents_not_ready") return "Some files are still being read. Wait until every file shows Ready.";
  if (error.type === "company_confirmation_required") return "Choose which company these files are about first.";
  if (error.status === 409) return error.detail || "An analysis of these files is already starting.";
  return error.detail || "The analysis could not be started. Try again.";
}

function CompanyChoice({ docs, onConfirmed }: { docs: DocumentStatus[]; onConfirmed: (accountId: string, company: Company) => void }) {
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
      onConfirmed(done.account_id, { name: name.trim(), domain });
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

/** Attach the files to the one company they name (creating its account when needed). */
interface Company { name: string; domain: string }

/** Files that finish reading after the company was confirmed join the same company, so no file
 * is left at "waiting for the company" once the choice has closed (2026-10-15). */
export function useAttachLate(waiting: DocumentStatus[], company: Company | null, accountId: string | null, onDone: () => void) {
  const sent = useRef(new Set<string>());
  useEffect(() => {
    if (!company || !accountId) return;
    const late = waiting.filter((d) => (d.stage === "parsed" || d.stage === "indexed") && !sent.current.has(d.document_id));
    if (!late.length) return;
    late.forEach((d) => sent.current.add(d.document_id));
    api<CompanyConfirmed>("POST", "/v1/documents/confirm-company", { document_ids: late.map((d) => d.document_id), company_name: company.name, domain: company.domain })
      .then(onDone)
      .catch(() => late.forEach((d) => sent.current.delete(d.document_id)));
  }, [waiting, company, accountId, onDone]);
}

function useAutoAttach(waiting: DocumentStatus[], settled: boolean, onAttached: (accountId: string, name: string, domain: string) => void) {
  const tried = useRef(new Set<string>());
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!settled || !waiting.length) return;
    const found = detectedCompany(waiting);
    const key = waiting.map((d) => d.document_id).sort().join(",");
    if (found.status !== "resolved" || !found.company?.domain || tried.current.has(key)) return;
    tried.current.add(key);
    const name = found.company.name ?? found.company.domain;
    api<CompanyConfirmed>("POST", "/v1/documents/confirm-company", { document_ids: waiting.map((d) => d.document_id), company_name: name, domain: found.company.domain })
      .then((done) => onAttached(done.account_id, name, found.company!.domain!))
      .catch(() => setFailed(true));
  }, [waiting, settled, onAttached]);
  return failed;
}

function FileTab({ initialDrive, estimate }: { initialDrive: boolean; estimate: ResearchEstimate | null }) {
  const router = useRouter();
  const [docs, setDocs] = useState<DocumentStatus[]>([]);
  const [accountId, setAccountId] = useState<string | null>(null);
  const [attached, setAttached] = useState<string | null>(null);
  const [company, setCompany] = useState<Company | null>(null);
  const [changing, setChanging] = useState(false);
  const [enrich, setEnrich] = useState(false);
  const [asking, setAsking] = useState(false);
  const [hit, setHit] = useState<{ kind: "researches" | "chats"; message: string } | null>(null);
  const { limits } = useMyLimits();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const onChange = useCallback((next: DocumentStatus[]) => setDocs(next), []);
  const waiting = needingCompany(docs);
  const ready = allIndexed(docs);
  const bound = accountId ?? docs.find((d) => d.account_id)?.account_id ?? null;
  const settled = waiting.length > 0 && docs.every((d) => d.stage === "parsed" || d.stage === "indexed" || d.stage === "failed");
  const onAttached = useCallback((id: string, name: string, domain: string) => { setAccountId(id); setAttached(name); setCompany({ name, domain }); setRefresh((n) => n + 1); }, []);
  const autoFailed = useAutoAttach(waiting, settled, onAttached);
  const reread = useCallback(() => setRefresh((n) => n + 1), []);
  useAttachLate(waiting, company, accountId, reread);
  const resolved = detectedCompany(waiting).status === "resolved";
  // once a company is confirmed the choice closes, even before the files report "indexed"
  const needsChoice = (!accountId && settled && (!resolved || autoFailed)) || changing;

  const uses = enrich ? "One chat and one research" : "One chat";

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
      const reached = limitReached(e);
      if (reached) setHit(reached);
      else setError(startError(e));
      key.current = null;
      setBusy(false);
    }
  };

  const usage = estimate ? (enrich ? estimate.file_public_usage : estimate.file_usage) : null;
  const analyze = () => {
    if (!ready || busy) return;
    if (usage && !asking) {
      setAsking(true);
      return;
    }
    void start();
  };

  return (
    <div className="stack-20">
      <DocumentDrop onChange={onChange} disabled={busy} initialDrive={initialDrive} refresh={refresh} />
      {attached && !changing ? (
        <div className="card pad-sm row-space wrap" role="status">
          <span>These files are about <b>{attached}</b>.</span>
          <button type="button" className="link-btn small" onClick={() => setChanging(true)}>Change</button>
        </div>
      ) : null}
      {needsChoice ? (
        <CompanyChoice
          docs={changing ? docs.filter((d) => d.stage === "parsed" || d.stage === "indexed") : waiting}
          onConfirmed={(id, chosen) => { setAccountId(id); setCompany(chosen); setAttached(null); setChanging(false); setRefresh((n) => n + 1); }}
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
              onClick={() => { setEnrich((v) => !v); setAsking(false); }} />
          </section>
          <div className="card pad-sm row-space wrap">
            <span className="mut">This analysis uses</span>
            <b>{uses}</b>
          </div>
          {hit ? <LimitReached kind={hit.kind} message={hit.message} pending={limits?.pending_requests.includes(hit.kind)} /> : null}
          {error && !asking ? <p className="sm-text bad-text" role="alert">{error}</p> : null}
          <LimitNote limits={limits} kind="chats" />
          {asking && usage && hit === null ? (
            <UsageProceed
              usage={usage}
              what="This analysis"
              busy={busy}
              message={error}
              note={enrich ? (
                <>
                  <p className="sm-text">Checking public sources researches the company on the web as well, and uses one of your researches.</p>
                  <LimitNote limits={limits} kind="researches" />
                </>
              ) : null}
              onProceed={() => void start()}
              onCancel={() => { setAsking(false); setError(null); }}
            />
          ) : (
            <div className="row-12 wrap">
              <button type="button" className="btn primary" disabled={!ready || busy || !bound || hit !== null} onClick={analyze}>
                {busy ? "Starting…" : "Analyze file"}
              </button>
              <span className="mut sm-text">Results are marked <b>From your files</b> and kept private to your workspace.</span>
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}

export function NewResearch({ estimate, initialTab, initialDrive = false }: { estimate: ResearchEstimate | null; initialTab: "company" | "file"; initialDrive?: boolean }) {
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
        <FileTab initialDrive={initialDrive} estimate={estimate} />
      )}
    </>
  );
}
