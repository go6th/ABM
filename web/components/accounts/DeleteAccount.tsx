"use client";
// Delete an account from the workspace, after asking twice: the button opens a confirmation
// panel that explains what goes (the account leaves every list; its research stays stored and
// comes back if the same domain is added again) and takes the domain typed back before the
// request is sent. The API checks the typed domain too, so a stray click can never delete.
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { AccountDeleted } from "@/lib/gen/api.gen";

export function DeleteAccount({ domain, name }: { domain: string; name: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputId = useId();
  const matches = typed.trim().toLowerCase() === domain.toLowerCase();

  const close = () => {
    if (busy) return;
    setOpen(false);
    setTyped("");
    setError(null);
  };
  const remove = async () => {
    if (!matches || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api<AccountDeleted>("DELETE", `/v1/accounts/${encodeURIComponent(domain)}`, { confirm: typed.trim() });
      router.push("/accounts");
      router.refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) setError("A research is still running on this account. Stop it, then delete the account.");
      else if (cause instanceof ApiError && cause.status === 404) { router.push("/accounts"); router.refresh(); return; }
      else setError("The account could not be deleted. Try again in a moment.");
      setBusy(false);
    }
  };

  return (
    <>
      <button type="button" className="btn danger" onClick={() => setOpen(true)} aria-haspopup="dialog" aria-expanded={open}>Delete account</button>
      {open ? (
        <div className="confirm-backdrop" onClick={close}>
          <div className="card pad stack-14 confirm-panel" role="dialog" aria-modal="true" aria-labelledby={`${inputId}-title`} onClick={(e) => e.stopPropagation()}>
            <h2 id={`${inputId}-title`} className="h-16">Delete {name}?</h2>
            <p className="sm-text">
              The account leaves your lists. Its research, files and history stay stored and come back if you add <b>{domain}</b> again.
            </p>
            <label htmlFor={inputId} className="lbl">Type the domain to confirm</label>
            <input id={inputId} className="in" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={domain} autoComplete="off" spellCheck={false} disabled={busy} />
            {error ? <p className="sm-text bad-text" role="alert">{error}</p> : null}
            <div className="row-12 wrap">
              <button type="button" className="btn danger" disabled={!matches || busy} aria-busy={busy} onClick={() => void remove()}>
                {busy ? "Deleting…" : "Delete this account"}
              </button>
              <button type="button" className="btn" disabled={busy} onClick={close}>Keep it</button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
