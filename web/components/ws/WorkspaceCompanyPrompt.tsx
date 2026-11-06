"use client";
// A workspace zip uploaded to an account whose domain is not the one the package names
// (2026-11-05): the server holds the upload and asks. "Yes, same company" binds the package's
// domain, name and careers host to this account and the import runs; "No" binds the file to the
// company the package names (the ordinary confirm-company call, which makes that account when it
// does not exist). Nothing is imported until one of them is pressed.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { CompanyConfirmed, DocumentStatus } from "@/lib/gen/api.gen";

export type WorkspaceIdentity = {
  domain: string;
  name?: string | null;
  research_date?: string | null;
  decision?: string | null;
  account_domain?: string | null;
  account_name?: string | null;
};

/** The package's identity on a held upload, else null. */
export function workspaceOf(doc: Pick<DocumentStatus, "company">): WorkspaceIdentity | null {
  const found = (doc.company ?? null) as { workspace?: WorkspaceIdentity | null } | null;
  const ws = found?.workspace ?? null;
  return ws && ws.domain && ws.decision === "mismatch" ? ws : null;
}

function label(name: string | null | undefined, domain: string | null | undefined): string {
  if (name && domain) return `${name} (${domain})`;
  return name ?? domain ?? "this account";
}

export function WorkspaceCompanyPrompt({ doc, ws, onDone }: { doc: DocumentStatus; ws: WorkspaceIdentity; onDone?: () => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState<"same" | "other" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const packageLabel = label(ws.name, ws.domain);
  const accountLabel = label(ws.account_name, ws.account_domain);

  const finish = () => {
    if (onDone) onDone();
    else router.refresh();
  };
  const same = async () => {
    setBusy("same");
    setError(null);
    try {
      await api<DocumentStatus>("POST", `/v1/documents/${encodeURIComponent(doc.document_id)}/same-company`);
      finish();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "The answer could not be saved. Try again.");
    } finally {
      setBusy(null);
    }
  };
  const other = async () => {
    setBusy("other");
    setError(null);
    try {
      await api<CompanyConfirmed>("POST", "/v1/documents/confirm-company", { document_ids: [doc.document_id], company_name: ws.name ?? ws.domain, domain: ws.domain });
      finish();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "The account could not be set. Try again.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="stack-8 needs-account" role="group" aria-label={`Which company is ${doc.name} about`} data-testid="workspace-company-prompt">
      <p className="small">
        <b>Is this the right account?</b> This workspace is about <b>{packageLabel}</b>, but it was uploaded to <b>{accountLabel}</b>. Nothing is imported until you answer.
      </p>
      <div className="row nowrap">
        <button type="button" className="btn" disabled={busy !== null} onClick={() => void same()}>
          {busy === "same" ? "Saving..." : `Yes, same company as ${accountLabel}`}
        </button>
        <button type="button" className="btn secondary" disabled={busy !== null} onClick={() => void other()}>
          {busy === "other" ? "Moving..." : `No, it belongs to ${packageLabel}`}
        </button>
      </div>
      <p className="small muted">Yes binds {ws.domain} to this account so its pages count as the account. No moves the file to {packageLabel}, made as a new account when it does not exist.</p>
      {error ? <p className="small" role="alert">{error}</p> : null}
    </div>
  );
}
