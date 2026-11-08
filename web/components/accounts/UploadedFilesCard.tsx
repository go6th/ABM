// The files the client uploaded for this account (owner request 2026-10-15): they are stored and
// bound to the account when the company is confirmed, but no screen listed them, so they looked
// lost. Names, sizes and status; a file's name opens the page that shows what was read from it
// (the account's domain is needed for that link; without it the name is plain text).
import Link from "next/link";
import type { DocumentStatus } from "@/lib/gen/api.gen";
import { STAGE_TEXT, fileSize, filesLine, progressLine, stageOf, stageTone } from "@/lib/documents";
import { cellText } from "@/lib/render";
import { Status } from "@/ui/primitives";
import { AccountPicker } from "@/components/ws/AccountPicker";
import { WorkspaceCompanyPrompt, workspaceOf } from "@/components/ws/WorkspaceCompanyPrompt";
import { DeleteFileButton } from "./DeleteFileButton";
import { WhatWasRead } from "./WhatWasRead";
import { ZipMembers } from "./ZipMembers";

export function UploadedFilesCard({ docs, domain = null }: { docs: DocumentStatus[]; domain?: string | null }) {
  const pageOf = (id: string) => (domain ? `/accounts/${encodeURIComponent(domain)}/documents/${encodeURIComponent(id)}` : null);
  return (
    <section className="card files-card" aria-labelledby="uploaded-title">
      <div className="lbl" id="uploaded-title">Your uploaded files</div>
      {docs.length === 0 ? (
        <p className="mut small files-empty">Files you upload for this account appear here.</p>
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
                  {d.audience === "owner_only" ? <span className="sub">Owner only: visible to you and admins</span> : null}
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
      <div className="files-actions">
        <Link className="btn" href="/accounts/import" prefetch={false}>Add files</Link>
      </div>
    </section>
  );
}
