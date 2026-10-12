"use client";
// Downloads for a researched account: the Word and Excel files and the account summary PDF.
// Each click builds a package on the server; delivered files appear as links.
import { useEffect, useState } from "react";
import { Download, FileText } from "lucide-react";
import { api } from "@/lib/api";
import { m } from "@/lib/messages";
import { cellText } from "@/lib/render";

interface Pkg { package_id: string; kind: string; status: string; reason: string | null; files: { index: number; name: string }[] }

export function Downloads({ domain }: { domain: string }) {
  const [packages, setPackages] = useState<Pkg[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Earlier packages for this account, newest first; one per kind is shown.
  useEffect(() => {
    let alive = true;
    api<{ packages: Pkg[] }>("GET", `/v1/exports?domain=${encodeURIComponent(domain)}`)
      .then((r) => {
        if (!alive) return;
        const latest = new Map<string, Pkg>();
        for (const p of r.packages) if (!latest.has(p.kind)) latest.set(p.kind, p);
        setPackages((now) => (now.length ? now : [...latest.values()]));
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [domain]);
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
    <section className="section" aria-labelledby="downloads">
      <div className="section-head"><h2 id="downloads">{m.downloads.title}</h2><a className="link t-footnote" href="/downloads">All files</a></div>
      <div className="downloads">
        <button type="button" className="btn" disabled={busy !== null} onClick={() => void build("files")}>
          <FileText size={16} strokeWidth={1.6} aria-hidden="true" />{m.downloads.files}
        </button>
        <button type="button" className="btn" disabled={busy !== null} onClick={() => void build("pdf")}>
          <FileText size={16} strokeWidth={1.6} aria-hidden="true" />{m.downloads.pdf}
        </button>
      </div>
      {busy ? <p className="t-footnote ink-2" role="status">{m.downloads.building}</p> : null}
      {error ? <p className="t-footnote inline-error" role="alert">{error}</p> : null}
      {packages.length ? (
        <ul className="rows files">
          {packages.flatMap((p) => p.status === "delivered"
            ? p.files.map((f) => (
              <li key={`${p.package_id}-${f.index}`}>
                <a className="list-row" href={`/v1/exports/${encodeURIComponent(p.package_id)}/files/${f.index}`}>
                  <span>{cellText(f.name)}</span><Download size={16} strokeWidth={1.6} aria-hidden="true" />
                </a>
              </li>
            ))
            : [<li key={p.package_id} className="list-row"><span className="inline-error">{m.downloads.notDelivered}</span></li>])}
        </ul>
      ) : null}
    </section>
  );
}
