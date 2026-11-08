"use client";
// Delete an account from the workspace, after asking twice: the button opens a confirmation
// panel that explains what goes (the account leaves every list; its research stays stored and
// comes back if the same domain is added again) and takes the domain typed back before the
// request is sent. The API checks the typed domain too, so a stray click can never delete.
import { useRouter } from "next/navigation";
import { useId, useRef, useState } from "react";
import { api, ApiError, errorText } from "@/lib/api";
import type { AccountDeleted } from "@/lib/gen/api.gen";
import { Modal } from "@/ui/Modal";
import { announce } from "@/ui/Toaster";

export function DeleteAccount({ domain, name, inMenu = false }: { domain: string; name: string; inMenu?: boolean }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
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
      announce(`${name} was deleted.`);
      router.push("/accounts");
      router.refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) setError("A research is still running on this account. Stop it, then delete the account.");
      else if (cause instanceof ApiError && cause.status === 404) { router.push("/accounts"); router.refresh(); return; }
      else setError(errorText(cause, "The account could not be deleted. Try again in a moment."));
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        className={inMenu ? "menu-item danger" : "btn danger"}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={(e) => { e.currentTarget.closest("details")?.removeAttribute("open"); setOpen(true); }}
      >
        Delete account
      </button>
      {open ? (
        <Modal title={`Delete ${name}?`} onClose={close} busy={busy} initialFocus={input}>
          <p className="sm-text">
            The account leaves your lists. Its research, files and history stay stored and come back if you add <b>{domain}</b> again.
          </p>
          <form className="stack-12" onSubmit={(e) => { e.preventDefault(); void remove(); }}>
            <div className="field">
              <label htmlFor={inputId}>Type <b>{domain}</b> to confirm</label>
              <input ref={input} id={inputId} className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={domain} autoComplete="off" spellCheck={false} disabled={busy} />
            </div>
            {error ? <p className="notice error" role="alert">{error}</p> : null}
            <div className="modal-actions">
              <button type="button" className="btn" disabled={busy} onClick={close}>Keep it</button>
              <button type="submit" className="btn primary danger" disabled={!matches && !busy} aria-busy={busy || undefined}>
                {busy ? "Deleting…" : "Delete this account"}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
    </>
  );
}
