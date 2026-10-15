// The files the client uploaded for this account (owner request 2026-10-15): they are stored and
// bound to the account when the company is confirmed, but no screen listed them, so they looked
// lost. Names, sizes and status only; a file never opens from here (it is read into passages).
import Link from "next/link";
import type { DocumentStatus } from "@/lib/gen/api.gen";
import { STAGE_TEXT, fileSize, stageOf, stageTone } from "@/lib/documents";
import { cellText } from "@/lib/render";
import { Status } from "@/ui/primitives";

export function UploadedFilesCard({ docs }: { docs: DocumentStatus[] }) {
  return (
    <section className="card files-card" aria-labelledby="uploaded-title">
      <div className="lbl" id="uploaded-title">Your uploaded files</div>
      {docs.length === 0 ? (
        <p className="mut small files-empty">Files you upload for this account appear here.</p>
      ) : (
        <ul className="rows uploads" aria-label="Your uploaded files">
          {docs.map((d) => {
            const stage = stageOf(d);
            return (
              <li key={d.document_id}>
                <div>
                  <p>{cellText(d.name)}</p>
                  <span className="sub">{fileSize(d.size_bytes)}{d.passages ? `. ${d.passages.toLocaleString()} passages` : ""}</span>
                </div>
                <Status tone={stageTone(stage)}>{STAGE_TEXT[stage]}</Status>
              </li>
            );
          })}
        </ul>
      )}
      <div className="files-actions">
        <Link className="btn" href="/research/new?tab=file" prefetch={false}>Add files</Link>
      </div>
    </section>
  );
}
