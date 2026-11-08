"use client";
// Delete an account and erase it (owner order 2026-11-08): the button opens a confirmation that
// says plainly what goes (its research, people, jobs, files and history, for good; adding the
// domain again starts empty) and asks for a tick, "I understand this cannot be undone", before
// the Delete button works. The API takes only that tick (confirm: true), so a stray click can
// never delete.
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { api, ApiError, errorText } from "@/lib/api";
import type { AccountDeleted } from "@/lib/gen/api.gen";
import { Modal } from "@/ui/Modal";
import { announce } from "@/ui/Toaster";

export function DeleteAccount({ domain, name, inMenu = false }: { domain: string; name: string; inMenu?: boolean }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tick = useRef<HTMLInputElement>(null);

  const close = () => {
    if (busy) return;
    setOpen(false);
    setUnderstood(false);
    setError(null);
  };
  const remove = async () => {
    if (!understood || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api<AccountDeleted>("DELETE", `/v1/accounts/${encodeURIComponent(domain)}`, { confirm: true });
      announce(`${name} was deleted.`);
      router.push("/accounts");
      router.refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) setError("A research is still running on this account. Stop it, then delete the account.");
      else if (cause instanceof ApiError && cause.status === 404) { router.push("/accounts"); router.refresh(); return; }
      else setError(errorText(cause, "The account could not be deleted. Nothing was changed. Try again in a moment."));
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
        <Modal title={`Delete ${name}?`} onClose={close} busy={busy} initialFocus={tick}>
          <p className="sm-text">
            This permanently erases <b>{domain}</b>: its research, people, jobs, files and history. It cannot be undone. If you add {domain} again, it starts empty.
          </p>
          <form className="stack-12" onSubmit={(e) => { e.preventDefault(); void remove(); }}>
            <label className="check">
              <input ref={tick} type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} disabled={busy} />
              I understand this cannot be undone
            </label>
            {error ? <p className="notice error" role="alert">{error}</p> : null}
            <div className="modal-actions">
              <button type="button" className="btn" disabled={busy} onClick={close}>Keep it</button>
              <button type="submit" className="btn primary danger" disabled={!understood && !busy} aria-busy={busy || undefined}>
                {busy ? "Deleting…" : "Delete permanently"}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
    </>
  );
}
