"use client";
// The research files on the account's Files tab: the deliverables with checkboxes (none ticked
// until the user picks: a download they did not choose is a surprise), Select all, "Download
// selected (N)" (one file downloads as itself, several as one zip) and "Download all". Each file
// shows its type (Word, Excel, PDF) as a badge. "Download everything" (deliverables, every table
// whole and your own files) is its own card in the right column since 2026-11-08 (owner: "in the
// right side, in a professional way").
import { useState } from "react";
import type { DeliverableFile } from "@/lib/gen/api.gen";

const TYPE: Record<string, { tag: string; tone: string }> = {
  docx: { tag: "DOC", tone: "doc" },
  doc: { tag: "DOC", tone: "doc" },
  xlsx: { tag: "XLS", tone: "xls" },
  xls: { tag: "XLS", tone: "xls" },
  csv: { tag: "CSV", tone: "xls" },
  pdf: { tag: "PDF", tone: "pdf" },
  pptx: { tag: "PPT", tone: "pdf" },
  zip: { tag: "ZIP", tone: "" },
};

/** The badge for a file: from its kind, else its file name's extension. */
export function fileType(f: Pick<DeliverableFile, "kind" | "filename">): { tag: string; tone: string } {
  const ext = (f.kind || f.filename.split(".").pop() || "").toLowerCase();
  return TYPE[ext] ?? { tag: ext.slice(0, 4).toUpperCase() || "FILE", tone: "" };
}

export function FilesCard({ domain, files: given }: { domain: string; files: DeliverableFile[] | null | undefined }) {
  const files = given ?? [];
  const [chosen, setPicked] = useState<string[]>([]);
  // after a reload with newer files, a pick of a file no longer listed is dropped
  const picked = chosen.filter((id) => files.some((f) => f.id === id));
  const base = `/v1/accounts/${encodeURIComponent(domain)}/deliverables/download?ids=`;
  const href = (ids: string[]) => base + ids.map(encodeURIComponent).join(",");
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const all = files.length > 0 && picked.length === files.length;
  return (
    <section className="card fc" aria-labelledby="files-title">
      <div className="fc-head">
        <div className="stack-4">
          <h2 id="files-title">Research files</h2>
          <span className="mut xs-text">
            {files.length ? `${files.length} file${files.length === 1 ? "" : "s"} from the latest research. Tick the ones you want, or download them all.` : "Built when a research finishes."}
          </span>
        </div>
        {files.length > 1 ? (
          <button type="button" className="link-btn small" onClick={() => setPicked(all ? [] : files.map((f) => f.id))}>
            {all ? "Clear selection" : "Select all"}
          </button>
        ) : null}
      </div>
      {files.length === 0 ? (
        <p className="mut sm-text fc-empty">Files appear here when research finishes.</p>
      ) : (
        <>
          <div className="fc-list" role="group" aria-labelledby="files-title">
            {files.map((f) => {
              const on = picked.includes(f.id);
              const type = fileType(f);
              return (
                <button key={f.id} type="button" className="fc-row" role="checkbox" aria-checked={on} onClick={() => toggle(f.id)}>
                  <span className={on ? "box on" : "box"} aria-hidden="true">✓</span>
                  <span className={`ftag ${type.tone}`} aria-hidden="true">{type.tag}</span>
                  <span className="fc-name">
                    <span className="strong">{f.name}</span>
                    <span className="mut xs-text">{f.meta}</span>
                  </span>
                </button>
              );
            })}
          </div>
          <div className="fc-actions">
            {picked.length ? (
              <a className="btn primary" href={href(picked)} download>Download selected ({picked.length})</a>
            ) : (
              <span className="btn primary" aria-disabled="true">Download selected (0)</span>
            )}
            <a className="btn" href={base + "all"} download>Download all</a>
          </div>
        </>
      )}
    </section>
  );
}
