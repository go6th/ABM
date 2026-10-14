"use client";
// Choose files from your own Google Drive for a "From a file" research (owner decision
// 2026-10-15). The list comes from the server (newest first, only types a research can read;
// the Drive token never reaches the browser). "Add" hands the file to the server, which
// downloads it into the same scan and reading path as an upload. Not connected: a link to the
// Mail and drive page, where each person connects their own Drive.
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { fileSize } from "@/lib/documents";
import { cellText } from "@/lib/render";
import { m } from "@/lib/messages";
import type { DriveFile, DriveFileList } from "@/lib/gen/api.gen";
import { Notice } from "@/ui/primitives";

const CONNECT = new Set(["drive_not_connected", "drive_broken"]);

function when(at: string | null): string {
  if (!at) return "";
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function DriveChooser({ onPick, picked, disabled = false }: {
  onPick: (file: DriveFile) => void;
  picked: ReadonlySet<string>;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [files, setFiles] = useState<DriveFile[] | null>(null);
  const [problem, setProblem] = useState<{ type: string; detail: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (q: string) => {
    setLoading(true);
    setProblem(null);
    try {
      const out = await api<DriveFileList>("GET", `/v1/documents/drive-files?q=${encodeURIComponent(q)}`);
      setFiles(out.files);
    } catch (error) {
      setFiles(null);
      setProblem(error instanceof ApiError
        ? { type: error.type, detail: error.detail }
        : { type: "drive_unreachable", detail: m.research.driveError });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => void load(""), 0);
    return () => clearTimeout(timer);
  }, [load]);

  if (problem && (CONNECT.has(problem.type) || problem.type === "not_available" || problem.type === "not_allowed")) {
    return (
      <div className="card quiet pad-sm stack-8" role="status">
        <p className="small">{problem.detail}</p>
        {CONNECT.has(problem.type) ? (
          <div><Link className="btn primary sm" href="/mail-and-drive" prefetch={false}>{m.research.driveConnectLink}</Link></div>
        ) : null}
      </div>
    );
  }

  return (
    <section className="card quiet pad-sm stack-8" aria-label={m.research.driveTitle}>
      <form
        className="row-12 wrap"
        role="search"
        onSubmit={(e) => { e.preventDefault(); void load(query); }}
      >
        <input
          className="input sm grow-240"
          type="search"
          value={query}
          maxLength={100}
          placeholder={m.research.driveSearch}
          aria-label={m.research.driveSearch}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit" className="btn sm" disabled={loading}>Search</button>
      </form>
      {problem ? <Notice kind="error">{problem.detail}</Notice> : null}
      {loading ? <p className="small muted" role="status">{m.research.driveLoading}</p> : null}
      {!loading && files && !files.length ? <p className="small muted">{m.research.driveEmpty}</p> : null}
      {!loading && files?.length ? (
        <ul className="rows uploads" aria-label={m.research.driveTitle}>
          {files.map((f) => (
            <li key={f.id}>
              <div>
                <p>{cellText(f.name)}</p>
                <span className="sub">
                  {f.google_native ? `Google file, added as ${f.type.toUpperCase()}` : f.type.toUpperCase()}
                  {f.size_bytes !== null ? `. ${fileSize(f.size_bytes)}` : ""}
                  {f.modified_at ? `. ${when(f.modified_at)}` : ""}
                </span>
              </div>
              <button type="button" className="btn sm" disabled={disabled || picked.has(f.id)} onClick={() => onPick(f)}>
                {picked.has(f.id) ? "Added" : "Add"}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
