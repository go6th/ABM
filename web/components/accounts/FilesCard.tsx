"use client";
// FILES on the Account workspace: the account's deliverables with checkboxes, "Download
// selected (N)" (one file downloads as itself, several as one zip), "Download all", and
// "Download everything" (deliverables, every table whole, and your own files).
import { useState } from "react";
import type { DeliverableFile } from "@/lib/gen/api.gen";
import { DownloadEverything } from "./DownloadEverything";

export function FilesCard({ domain, files: given }: { domain: string; files: DeliverableFile[] | null | undefined }) {
  const files = given ?? [];
  const [chosen, setPicked] = useState<string[]>(() => files.slice(0, 2).map((f) => f.id));
  // after a reload with newer files, a pick of a file no longer listed is dropped
  const picked = chosen.filter((id) => files.some((f) => f.id === id));
  const base = `/v1/accounts/${encodeURIComponent(domain)}/deliverables/download?ids=`;
  const href = (ids: string[]) => base + ids.map(encodeURIComponent).join(",");
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  return (
    <section className="card files-card" aria-labelledby="files-title">
      <div className="lbl" id="files-title">Files</div>
      {files.length === 0 ? (
        <p className="mut small files-empty">Files appear here when research finishes.</p>
      ) : (
        <>
          <div className="files-group" role="group" aria-labelledby="files-title">
          {files.map((f) => {
            const on = picked.includes(f.id);
            return (
              <button key={f.id} type="button" className="fl" role="checkbox" aria-checked={on} onClick={() => toggle(f.id)}>
                <span className="in">
                  <span className={on ? "box on" : "box"} aria-hidden="true">✓</span>
                  <span className="grow strong">{f.name}<br /><span className="mut meta">{f.meta}</span></span>
                </span>
              </button>
            );
          })}
          </div>
          <div className="files-actions">
            {picked.length ? (
              <a className="btn primary" href={href(picked)} download>Download selected ({picked.length})</a>
            ) : (
              <span className="btn primary" aria-disabled="true">Download selected (0)</span>
            )}
            <a className="btn" href={base + "all"} download>Download all</a>
          </div>
        </>
      )}
      <div className="files-actions"><DownloadEverything domain={domain} /></div>
    </section>
  );
}
