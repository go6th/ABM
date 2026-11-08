"use client";
// "Add files" on an account's Files tab (owner request 2026-11-08): the files go into THIS account
// only. It used to open Import a file (/accounts/import), which asks which company the file is
// about and can create a new account. Here the pop-up uploads with the account's id, so the server
// binds each file to it from the start (``documents.service``: an upload sent to an account is
// read, imported and indexed there). The one exception the server keeps: a file that clearly names
// another company waits as "Needs account", and the picker under it is preselected with this
// account, so one press keeps it here. Nothing is analysed or researched by itself (automatic
// starts are off), so adding files spends nothing.
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import type { DocumentStatus } from "@/lib/gen/api.gen";
import { Modal } from "@/ui/Modal";
import { DocumentDrop } from "@/components/ws/DocumentDrop";

export interface FilesAccount { id: string; domain: string; name: string | null }

export function AddFilesToAccount({ account }: { account: FilesAccount }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [added, setAdded] = useState(0);
  const [sending, setSending] = useState(false);
  const name = account.name || account.domain;
  const onChange = useCallback((docs: DocumentStatus[]) => setAdded(docs.length), []);
  const close = () => {
    setOpen(false);
    // the card behind lists the new files and keeps itself current while they are read
    if (added) router.refresh();
    setAdded(0);
  };
  return (
    <>
      <button type="button" className="btn" onClick={() => setOpen(true)}>Add files</button>
      {open ? (
        <Modal title={`Add files to ${name}`} onClose={close} busy={sending} wide>
          <p className="mut sm-text">
            Files you add here go into <b>{name}</b> only, and stay private to your workspace. They are read and added to this
            account&rsquo;s people, jobs and technology; nothing is researched or charged.
          </p>
          <DocumentDrop accountId={account.id} onChange={onChange} onSending={setSending} />
          <div className="row-12 wrap">
            <span className="mut sm-text grow-220" aria-live="polite">
              {sending ? "Sending your files. Keep this open until they are sent." : added ? `${added} file${added === 1 ? "" : "s"} added. Reading carries on after you close this.` : ""}
            </span>
            <button type="button" className="btn primary" disabled={sending} onClick={close}>Done</button>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
