// The files the client uploaded for this account (owner request 2026-10-15): they are stored and
// bound to the account when the company is confirmed, but no screen listed them, so they looked
// lost. Names, sizes and status; a file's name opens the page that shows what was read from it
// (the account's domain is needed for that link; without it the name is plain text). With the
// account, "Add files" uploads into it in a pop-up (2026-11-08); without it, it opens Import a file.
import Link from "next/link";
import type { DocumentStatus } from "@/lib/gen/api.gen";
import { STAGE_TEXT, fileSize, filesLine, progressLine, stageOf, stageTone } from "@/lib/documents";
import { cellText } from "@/lib/render";
import { Status } from "@/ui/primitives";
import { AccountPicker } from "@/components/ws/AccountPicker";
import { WorkspaceCompanyPrompt, workspaceOf } from "@/components/ws/WorkspaceCompanyPrompt";
import { AddFilesToAccount, type FilesAccount } from "./AddFilesToAccount";
import { DeleteFileButton } from "./DeleteFileButton";
import { WhatWasRead } from "./WhatWasRead";
import { ZipMembers } from "./ZipMembers";

export function UploadedFilesCard({ docs, domain = null, account = null }: { docs: DocumentStatus[]; domain?: string | null; account?: FilesAccount | null }) {
  const pageOf = (id: string) => (domain ? `/accounts/${encodeURIComponent(domain)}/documents/${encodeURIComponent(id)}` : null);
  return (
    <section className="card fc" aria-labelledby="uploaded-title">
      <div className="fc-head">
        <div className="stack-4">
          <h2 id="uploaded-title">Your uploaded files</h2>
          <span className="mut xs-text">Private to your workspace. Never shared in research files.</span>
        </div>
      </div>
      {docs.length === 0 ? (
        <p className="mut sm-text fc-empty">Files you upload for this account appear here.</p>
      ) : (
        <ul className="rows uploads" aria-label="Your uploaded files">
          {docs.map((d) => {
            const stage = stageOf(d);
            const page = pageOf(d.document_id);
            return (
              <li key={d.document_id}>
                <div className="stack-8">
                  <p>{page ? <Link href={page} prefetch={false}>{cellText(d.name)}</Link> : cellText(d.name)}</p>
                  <span className="sub">{fileSize(d.size_bytes)}{d.passages ? `. ${d.passages.toLocaleString()} sections` : ""}</span>
                  {progressLine(d) ? <span className="sub" aria-live="polite">{progressLine(d)}</span> : null}
                  {filesLine(d) ? <span className="sub">{filesLine(d)}</span> : null}
                  {d.members_skipped?.length ? (
                    <details className="sub">
                      <summary>Skipped files</summary>
                      <ul>{d.members_skipped.map((m) => <li key={m.member}>{cellText(m.member)}: {m.reason}</li>)}</ul>
                    </details>
                  ) : null}
                  {(d.members_read ?? 0) + (d.members_skipped?.length ?? 0) > 0 ? <ZipMembers documentId={d.document_id} name={d.name} page={page} /> : null}
                  {d.passages ? <WhatWasRead documentId={d.document_id} name={d.name} /> : null}
                  {stage === "needs_account" ? (workspaceOf(d) ? <WorkspaceCompanyPrompt doc={d} ws={workspaceOf(d)!} /> : <AccountPicker doc={d} />) : null}
                  <DeleteFileButton documentId={d.document_id} name={d.name} />
                </div>
                <Status tone={stageTone(stage)}>{STAGE_TEXT[stage]}</Status>
              </li>
            );
          })}
        </ul>
      )}
      <div className="fc-actions">
        {account ? <AddFilesToAccount account={account} /> : <Link className="btn" href="/accounts/import" prefetch={false}>Add files</Link>}
      </div>
    </section>
  );
}
