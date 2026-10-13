"use client";
// Files: the account's deliverables with type and size, checkboxes, Download selected and
// Download all. One file downloads as itself; several download one after another until the API
// offers a zip. Build files asks the server for the standing Word and Excel set.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { m } from "@/lib/messages";
import { cellText } from "@/lib/render";
import type { ExportPackages, ExportPackage } from "@/lib/gen/api.gen";
import { EmptyState, Loading, Notice } from "@/ui/primitives";

interface FileRow { id: string; name: string; kind: string; size: number; href: string }

function size(bytes: number): string {
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function rowsOf(packages: ExportPackage[]): FileRow[] {
  const latest = new Map<string, ExportPackage>();
  for (const p of packages) if (p.status === "delivered" && !latest.has(p.kind)) latest.set(p.kind, p);
  return [...latest.values()].flatMap((p) => p.files.map((f) => ({
    id: `${p.package_id}:${f.index}`, name: f.name, kind: f.kind, size: f.size, href: `/v1/exports/${encodeURIComponent(p.package_id)}/files/${f.index}`,
  })));
}

export function FilesTab({ domain }: { domain: string }) {
  const [rows, setRows] = useState<FileRow[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api<ExportPackages>("GET", `/v1/exports?domain=${encodeURIComponent(domain)}`)
      .then((r) => { if (alive) setRows(rowsOf(r.packages)); })
      .catch(() => { if (alive) setError(m.state.errorTitle); });
    return () => { alive = false; };
  }, [domain]);
  const build = async () => {
    setBusy(true);
    setError(null);
    try {
      const pkg = await api<ExportPackage>("POST", "/v1/exports", { domain, kind: "files" });
      if (pkg.status !== "delivered") setError(m.account.buildFailed);
      const all = await api<ExportPackages>("GET", `/v1/exports?domain=${encodeURIComponent(domain)}`);
      setRows(rowsOf(all.packages));
    } catch {
      setError(m.account.buildFailed);
    } finally {
      setBusy(false);
    }
  };
  const download = (list: FileRow[]) => {
    list.forEach((f, i) => setTimeout(() => {
      const a = document.createElement("a");
      a.href = f.href;
      a.download = f.name;
      a.click();
    }, i * 400));
  };
  if (error && !rows) return <Notice kind="error">{error}</Notice>;
  if (!rows) return <Loading />;
  const chosen = rows.filter((r) => selected.has(r.id));
  return (
    <div className="stack">
      <div className="actions">
        <button type="button" className="btn primary" disabled={!chosen.length} onClick={() => download(chosen)}>{m.account.downloadSelected}{chosen.length ? ` (${chosen.length})` : ""}</button>
        <button type="button" className="btn" disabled={!rows.length} onClick={() => download(rows)}>{m.account.downloadAll}</button>
        <button type="button" className="btn" disabled={busy} onClick={() => void build()}>{busy ? m.account.building : m.account.build}</button>
      </div>
      {error ? <p className="error-text small" role="alert">{error}</p> : null}
      {rows.length === 0 ? <EmptyState title={m.account.filesEmpty} /> : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col"><span className="sr-only">Select</span></th>
                <th scope="col">{m.account.filesTitle}</th>
                <th scope="col">{m.account.type}</th>
                <th scope="col" className="num">{m.account.size}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td><label className="check"><input type="checkbox" checked={selected.has(r.id)} onChange={(e) => setSelected((s) => { const n = new Set(s); if (e.target.checked) n.add(r.id); else n.delete(r.id); return n; })} /><span className="sr-only">{cellText(r.name)}</span></label></td>
                  <td><a href={r.href}>{cellText(r.name)}</a></td>
                  <td>{cellText(r.kind)}</td>
                  <td className="num">{size(r.size)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
