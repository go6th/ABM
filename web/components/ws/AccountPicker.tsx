"use client";
// A held upload ("Needs account", ingestion hardening PR5): the server could not match the file's
// company to an account, so nothing is imported or indexed until the user picks one. Shows the
// detected company and the user's accounts; "Use this account" confirms it (the same
// confirm-company call a new research makes), after which the server imports and indexes the file.
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { detectedLine } from "@/lib/documents";
import type { AccountPage, CompanyConfirmed, DocumentStatus } from "@/lib/gen/api.gen";

type Option = { id?: string | null; domain: string; name: string | null };

export function AccountPicker({ doc, onConfirmed, disabled = false }: { doc: DocumentStatus; onConfirmed?: () => void; disabled?: boolean }) {
  const router = useRouter();
  const [options, setOptions] = useState<Option[] | null>(null);
  const [chosen, setChosen] = useState<string>(doc.account_id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const detected = detectedLine(doc);

  useEffect(() => {
    let live = true;
    api<AccountPage>("GET", "/v1/accounts?limit=200")
      .then((page) => { if (live) setOptions(page.accounts); })
      .catch(() => { if (live) setOptions([]); });
    return () => { live = false; };
  }, []);

  const pick = options?.find((o) => o.id === chosen) ?? null;
  const confirm = async () => {
    if (!pick) return;
    setBusy(true);
    setError(null);
    try {
      await api<CompanyConfirmed>("POST", "/v1/documents/confirm-company", { document_ids: [doc.document_id], company_name: pick.name ?? pick.domain, domain: pick.domain });
      if (onConfirmed) onConfirmed();
      else router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "The account could not be set. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack-8 needs-account" role="group" aria-label={`Pick the account for ${doc.name}`}>
      <p className="small">{doc.message ?? "Pick the account this file belongs to."}</p>
      {detected ? <p className="small muted">The file looks like it is about {detected}.</p> : <p className="small muted">No company could be matched from the file.</p>}
      <div className="row nowrap">
        <select aria-label={`Account for ${doc.name}`} value={chosen} disabled={disabled || busy || !options} onChange={(e) => setChosen(e.target.value)}>
          <option value="">Choose an account</option>
          {(options ?? []).map((o) => <option key={o.id ?? o.domain} value={o.id ?? ""}>{o.name ? `${o.name} (${o.domain})` : o.domain}</option>)}
        </select>
        <button type="button" className="btn" disabled={disabled || busy || !pick} onClick={() => void confirm()}>Use this account</button>
      </div>
      {error ? <p className="small" role="alert">{error}</p> : null}
    </div>
  );
}
