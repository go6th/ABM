"use client";
// New research: two tabs. Company name researches a company from public sources (a deep run).
// From a file researches only the chosen files; public sources only when turned on. The server
// decides the company check and the estimate; nothing here guesses which company a file is about.
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { DocumentDrop } from "@/components/ws/DocumentDrop";
import { Field, Notice, PageHead } from "@/ui/primitives";
import { m } from "@/lib/messages";
import { DOMAIN_RE, allIndexed, cleanDomain, detectedCompany, needingCompany, usd, type CompanyCandidate } from "@/lib/documents";
import type { CompanyConfirmed, DocumentEstimate, DocumentStatus, RunCreated } from "@/lib/gen/api.gen";

type Mode = "company" | "file";

function startError(error: unknown): string {
  if (!(error instanceof ApiError)) return "The research could not be started. Try again.";
  if (error.type === "documents_not_ready") return "Some files are still being read. Wait until every file shows Ready.";
  if (error.type === "company_confirmation_required") return "Confirm which company these files are about first.";
  if (error.type === "domain_required") return "Add the company's website, for example acme.com.";
  if (error.type === "excluded_company") return "This company is on your workspace's exclusion list.";
  if (error.status === 409) return "A run for this account is already starting. Try again in a moment.";
  return error.detail || "The research could not be started. Try again.";
}

function CompanyForm({ initialName, initialDomain }: { initialName: string; initialDomain: string }) {
  const router = useRouter();
  const [name, setName] = useState(initialName);
  const [domain, setDomain] = useState(initialDomain);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const clean = cleanDomain(domain);
  const domainOk = !domain.trim() || DOMAIN_RE.test(clean);
  const start = async () => {
    setBusy(true);
    setError(null);
    key.current ??= newIdempotencyKey();
    try {
      const created = await api<RunCreated>("POST", "/v1/runs", {
        input_mode: "company", company: { company_name: name.trim(), domain: clean || null, credit_cap: null },
      }, { "Idempotency-Key": key.current });
      router.push(`/runs/${encodeURIComponent(created.run_id)}`);
    } catch (e) {
      setError(startError(e));
      key.current = null;
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="form" onSubmit={(e) => { e.preventDefault(); if (name.trim() && domainOk && !busy) void start(); }}>
      <Field id="company-name" label={m.research.name}>
        <input id="company-name" className="input" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field id="company-domain" label={m.research.website} hint={m.research.websiteHint} error={!domainOk ? m.research.websiteError : null}>
        <input id="company-domain" className="input" maxLength={253} value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="acme.com" aria-invalid={!domainOk} />
      </Field>
      <p className="small muted"><span className="strong">{m.research.estimateLabel}.</span> {m.research.companyEstimate}</p>
      {error ? <Notice kind="error">{error}</Notice> : null}
      <div className="actions"><button type="submit" className="btn primary" disabled={busy || !name.trim() || !domainOk}>{m.research.start}</button></div>
    </form>
  );
}

function CompanyConfirm({ docs, presetName, presetDomain, onConfirmed }: {
  docs: DocumentStatus[]; presetName: string; presetDomain: string; onConfirmed: (accountId: string) => void;
}) {
  const detected = detectedCompany(docs);
  const first: CompanyCandidate | null = detected.company ?? detected.candidates[0] ?? null;
  const [name, setName] = useState(presetName || first?.name || "");
  const [domain, setDomain] = useState(presetDomain || first?.domain || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clean = cleanDomain(domain);
  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const done = await api<CompanyConfirmed>("POST", "/v1/documents/confirm-company", { document_ids: docs.map((d) => d.document_id), company_name: name.trim(), domain: clean });
      onConfirmed(done.account_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "The company could not be confirmed. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <fieldset className="form">
      <legend className="strong">{detected.status === "ambiguous" ? m.research.whichCompany : detected.status === "resolved" && detected.company ? m.research.whichCompanyOne(detected.company.name ?? detected.company.domain ?? "") : m.research.whichCompanyNone}</legend>
      {detected.candidates.length > 1 ? (
        <div>
          {detected.candidates.map((c) => (
            <label key={`${c.domain}-${c.name}`} className="choice">
              <input type="radio" name="candidate" checked={cleanDomain(domain) === (c.domain ?? "") && name === (c.name ?? "")} onChange={() => { setName(c.name ?? ""); setDomain(c.domain ?? ""); }} />
              <span className="grow"><span className="strong">{c.name ?? c.domain}</span><span className="hint block">{c.domain ?? "No website found"}</span></span>
              {c.mentions ? <span className="end">Mentioned {c.mentions} times</span> : null}
            </label>
          ))}
        </div>
      ) : null}
      <div className="form-row">
        <Field id="confirm-name" label={m.research.name}><input id="confirm-name" className="input" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field id="confirm-domain" label="Website"><input id="confirm-domain" className="input" maxLength={253} value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="acme.com" /></Field>
      </div>
      {error ? <Notice kind="error">{error}</Notice> : null}
      <div className="actions"><button type="button" className="btn primary" disabled={busy || !name.trim() || !DOMAIN_RE.test(clean)} onClick={() => void confirm()}>{m.research.confirmCompany}</button></div>
    </fieldset>
  );
}

function FileForm({ presetName, presetDomain, presetEnrich }: { presetName: string; presetDomain: string; presetEnrich: boolean }) {
  const router = useRouter();
  const [docs, setDocs] = useState<DocumentStatus[]>([]);
  const [accountId, setAccountId] = useState<string | null>(null);
  const [enrich, setEnrich] = useState(presetEnrich);
  const [fetched, setEstimate] = useState<DocumentEstimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const onChange = useCallback((next: DocumentStatus[]) => setDocs(next), []);
  const waiting = needingCompany(docs);
  const ready = allIndexed(docs);
  const boundAccount = accountId ?? docs.find((d) => d.account_id)?.account_id ?? null;
  const estimate = ready ? fetched : null;

  useEffect(() => {
    if (!ready) return;
    let live = true;
    api<DocumentEstimate>("POST", "/v1/documents/estimate", { document_ids: docs.map((d) => d.document_id), enrich_public: enrich, credit_cap: null })
      .then((e) => { if (live) setEstimate(e); }).catch(() => { if (live) setEstimate(null); });
    return () => { live = false; };
  }, [ready, docs, enrich]);

  const start = async () => {
    setBusy(true);
    setError(null);
    key.current ??= newIdempotencyKey();
    try {
      const created = await api<RunCreated>("POST", "/v1/runs", {
        input_mode: "file", file: { document_ids: docs.map((d) => d.document_id), account_id: boundAccount, enrich_public: enrich, credit_cap: null },
      }, { "Idempotency-Key": key.current });
      router.push(`/runs/${encodeURIComponent(created.run_id)}`);
    } catch (e) {
      setError(startError(e));
      key.current = null;
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="form">
      <p className="muted">{m.research.fileIntro}</p>
      <DocumentDrop onChange={onChange} disabled={busy} />
      {waiting.length && waiting.every((d) => d.stage === "parsed") && docs.every((d) => d.stage === "parsed" || d.stage === "indexed" || d.stage === "failed") ? (
        <CompanyConfirm docs={waiting} presetName={presetName} presetDomain={presetDomain} onConfirmed={setAccountId} />
      ) : null}
      {docs.length ? (
        <>
          <label className="check">
            <input type="checkbox" checked={enrich} onChange={(e) => setEnrich(e.target.checked)} />
            <span>{m.research.enrich}<span className="hint block">{m.research.enrichHint}</span></span>
          </label>
          {estimate ? (
            <dl className="dl" aria-label={m.research.estimateLabel}>
              <div className="contents"><dt>{m.research.estimateLabel}</dt><dd>{estimate.documents} files, {estimate.passages} passages</dd></div>
              <div className="contents"><dt>Model reading, about</dt><dd>{usd(estimate.model_usd)}</dd></div>
              {estimate.enrichment ? <div className="contents"><dt>Public sources</dt><dd>Approved at each checkpoint</dd></div> : null}
            </dl>
          ) : <p className="small muted">{ready ? m.research.estimateWorking : m.research.estimateWait}</p>}
          {error ? <Notice kind="error">{error}</Notice> : null}
          <div className="actions">
            <button type="button" className="btn primary" disabled={busy || !ready} onClick={() => void start()}>{m.research.start}</button>
            <span className="small muted">{m.research.privateNote}</span>
          </div>
        </>
      ) : null}
    </div>
  );
}

function NewResearch() {
  const params = useSearchParams();
  const [mode, setMode] = useState<Mode>(params.get("mode") === "file" ? "file" : "company");
  return (
    <div className="page narrow">
      <PageHead title={m.research.title} />
      <div className="seg" role="tablist" aria-label="Research input">
        <button type="button" role="tab" aria-selected={mode === "company"} onClick={() => setMode("company")}>{m.research.company}</button>
        <button type="button" role="tab" aria-selected={mode === "file"} onClick={() => setMode("file")}>{m.research.file}</button>
      </div>
      {mode === "company"
        ? <CompanyForm initialName={params.get("name") ?? ""} initialDomain={params.get("domain") ?? ""} />
        : <FileForm presetName={params.get("name") ?? ""} presetDomain={params.get("domain") ?? ""} presetEnrich={params.get("enrich") === "1"} />}
    </div>
  );
}

export default function NewResearchPage() {
  return <Suspense><NewResearch /></Suspense>;
}
