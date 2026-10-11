"use client";

import { useState, type FormEvent, type ReactNode } from "react";

export type SupportLookup = { grantId: string; runId?: string };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function SupportGrantLookup({
  runRequired = false,
  children,
}: {
  runRequired?: boolean;
  children: (lookup: SupportLookup | null) => ReactNode;
}) {
  const [grantId, setGrantId] = useState("");
  const [runId, setRunId] = useState("");
  const [submitted, setSubmitted] = useState<SupportLookup | null>(null);
  const [error, setError] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!UUID.test(grantId.trim())) {
      setError("Enter the UUID of an active tenant-approved support grant.");
      return;
    }
    if (runRequired && !UUID.test(runId.trim())) {
      setError("Enter a valid run UUID.");
      return;
    }
    setError("");
    setSubmitted({ grantId: grantId.trim(), ...(runRequired ? { runId: runId.trim() } : {}) });
  }

  return (
    <>
      <form className="card form" onSubmit={submit} aria-label="Support grant lookup">
        <div className="form-row">
          <div className="field">
            <label htmlFor="support-grant-id">Tenant-approved support grant ID</label>
            <input id="support-grant-id" className="input mono" name="grant_id" autoComplete="off" spellCheck={false} value={grantId} onChange={(event) => setGrantId(event.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" required />
          </div>
          {runRequired ? (
            <div className="field">
              <label htmlFor="operator-run-id">Run ID</label>
              <input id="operator-run-id" className="input mono" name="run_id" autoComplete="off" spellCheck={false} value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" required />
            </div>
          ) : null}
          <button className="btn primary" type="submit">Load tenant data</button>
        </div>
        {error ? <p className="field-error" role="alert">{error}</p> : null}
        <p className="hint">The grant goes only with this request and is not saved in the browser. The API checks its tenant, scope, expiry, revocation and staff owner on every read.</p>
      </form>
      {children(submitted)}
    </>
  );
}
