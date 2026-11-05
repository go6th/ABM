"use client";
// The files inside an uploaded zip (workspace import phase 1, 2026-11-03): one line per member
// with its role in the package, whether it was read, skipped or failed and why, and what it
// produced. Loaded only when opened, from GET /v1/documents/{id}/members.
import { useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { DocumentMembers } from "@/lib/gen/api.gen";
import { ROLE_TEXT, memberLine } from "@/lib/documents";
import { cellText } from "@/lib/render";

export { ROLE_TEXT, memberLine };

// page: the document's own page, when the card knows the account (each member links to its section)
export function ZipMembers({ documentId, name, page = null }: { documentId: string; name: string; page?: string | null }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<DocumentMembers | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api<DocumentMembers>("GET", `/v1/documents/${encodeURIComponent(documentId)}/members`));
    } catch (e) {
      setError(e instanceof ApiError && e.detail ? e.detail : "The files inside could not be listed. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const toggle = () => {
    const opening = !open;
    setOpen(opening);
    if (opening && !data && !busy) void load();
  };

  return (
    <div className="stack-8">
      <button type="button" className="link-btn small align-start" aria-expanded={open} onClick={toggle}>
        {open ? "Hide the files inside" : "View the files inside"}
      </button>
      {open ? (
        <section className="card quiet pad-sm stack-8" aria-label={`Files inside ${name}`}>
          {error ? <p className="sm-text bad-text" role="alert">{error}</p> : null}
          {!data && busy ? <p className="small muted" role="status">Listing the files inside</p> : null}
          {data ? (
            <>
              <p className="sub">
                {data.members.length.toLocaleString()} files: {data.read.toLocaleString()} read, {data.skipped.toLocaleString()} skipped, {data.failed.toLocaleString()} failed
              </p>
              <ol className="rows uploads" aria-label={`Files inside ${name}`}>
                {data.members.map((m, i) => (
                  <li key={m.member}>
                    <div>
                      <span className="sub">{ROLE_TEXT[m.role] ?? "File"}{m.audience === "owner_only" ? " · owner only" : ""}</span>
                      <p className="small">{page ? <Link href={`${page}#m-${i}`} prefetch={false}>{cellText(m.member)}</Link> : cellText(m.member)}</p>
                      <span className="sub">
                        {memberLine(m)}
                        {" · "}
                        <a href={`/v1/documents/${encodeURIComponent(documentId)}/members/${encodeURIComponent(m.id)}`} download>Download this file</a>
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
            </>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
