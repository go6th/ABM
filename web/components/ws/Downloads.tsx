"use client";
// Downloads for a researched account: the Word and Excel files and the account summary PDF.
// Each click builds a package on the server; delivered files appear as links.
import { useState } from "react";
import { Download, FileText } from "lucide-react";
import { api } from "@/lib/api";
import { m } from "@/lib/messages";
import { cellText } from "@/lib/render";

interface Pkg { package_id: string; kind: string; status: string; reason: string | null; files: { index: number; name: string }[] }

export function Downloads({ domain }: { domain: string }) {
  const [packages, setPackages] = useState<Pkg[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const build = async (kind: "files" | "pdf") => {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      const pkg = await api<Pkg>("POST", "/v1/exports", { domain, kind });
      setPackages((all) => [pkg, ...all.filter((p) => p.kind !== pkg.kind)]);
    } catch {
      setError(m.downloads.failed);
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="ws-section" aria-labelledby="downloads">
      <h2 id="downloads">{m.downloads.title}</h2>
      <div className="ws-downloads">
        <button type="button" className="ws-btn" disabled={busy !== null} onClick={() => void build("files")}>
          <FileText size={16} strokeWidth={1.6} aria-hidden="true" />{m.downloads.files}
        </button>
        <button type="button" className="ws-btn" disabled={busy !== null} onClick={() => void build("pdf")}>
          <FileText size={16} strokeWidth={1.6} aria-hidden="true" />{m.downloads.pdf}
        </button>
      </div>
      {busy ? <p className="ws-small ws-muted" role="status">{m.downloads.building}</p> : null}
      {error ? <p className="ws-small ws-error" role="alert">{error}</p> : null}
      {packages.length ? (
        <ul className="ws-rows ws-files">
          {packages.flatMap((p) => p.status === "delivered"
            ? p.files.map((f) => (
              <li key={`${p.package_id}-${f.index}`}>
                <a className="ws-row" href={`/v1/exports/${encodeURIComponent(p.package_id)}/files/${f.index}`}>
                  <span>{cellText(f.name)}</span><Download size={16} strokeWidth={1.6} aria-hidden="true" />
                </a>
              </li>
            ))
            : [<li key={p.package_id} className="ws-row"><span className="ws-error">{m.downloads.notDelivered}</span></li>])}
        </ul>
      ) : null}
    </section>
  );
}
