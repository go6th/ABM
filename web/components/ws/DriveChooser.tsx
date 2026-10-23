"use client";
// Choose files from your own Google Drive or OneDrive for a "From a file" research (owner
// decision 2026-10-15). The switch offers the drives connected on the Mail and drive page (one
// that is not connected says so, with a link there). The list comes from the server (newest
// first, only types a research can read; the drive token never reaches the browser). Tick files
// and "Add N files" hands them to the server, which downloads each into the same scan and
// reading path as an upload.
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { fileSize } from "@/lib/documents";
import { cellText } from "@/lib/render";
import { m } from "@/lib/messages";
import type { DriveFile, DriveFileList, MailAndDrive } from "@/lib/gen/api.gen";
import { Notice, Status } from "@/ui/primitives";

export type DriveSource = "google_drive" | "onedrive";

export const DRIVE_SOURCES: readonly DriveSource[] = ["google_drive", "onedrive"];
export const DRIVE_LABEL: Record<DriveSource, string> = { google_drive: "Google Drive", onedrive: "OneDrive" };
const CONNECT_LINK: Record<DriveSource, string> = { google_drive: m.research.driveConnectLink, onedrive: m.research.driveConnectOneDrive };
const CONNECT = new Set(["drive_not_connected", "drive_broken"]);
const STOP = new Set([...CONNECT, "not_available", "not_allowed"]);

function when(at: string | null): string {
  if (!at) return "";
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** The drives the switch enables: the one connected (or broken, so the server can say
 * "Reconnect") on the Mail and drive page. Unknown (the page could not be read): every drive,
 * and the server answers for each. */
export function enabledSources(page: unknown): ReadonlySet<DriveSource> {
  const drive = (page as Partial<MailAndDrive> | null)?.drive;
  if (!drive || typeof drive.status !== "string") return new Set(DRIVE_SOURCES);
  const kind = drive.kind as DriveSource | null;
  const on = (drive.status === "connected" || drive.status === "broken") && kind && DRIVE_SOURCES.includes(kind);
  return new Set(on ? [kind] : []);
}

export function DriveChooser({ onPick, picked, disabled = false }: {
  onPick: (files: DriveFile[], source: DriveSource) => void;
  picked: ReadonlySet<string>;
  disabled?: boolean;
}) {
  const [enabled, setEnabled] = useState<ReadonlySet<DriveSource> | null>(null);
  const [source, setSource] = useState<DriveSource>("google_drive");
  const [query, setQuery] = useState("");
  const [files, setFiles] = useState<DriveFile[] | null>(null);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set());
  const [problem, setProblem] = useState<{ type: string; detail: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const label = DRIVE_LABEL[source];

  const load = useCallback(async (from: DriveSource, q: string) => {
    setLoading(true);
    setProblem(null);
    setChosen(new Set());
    try {
      const params = new URLSearchParams({ q });
      if (from !== "google_drive") params.set("source", from);
      const out = await api<DriveFileList>("GET", `/v1/documents/drive-files?${params.toString()}`);
      setFiles(out.files);
    } catch (error) {
      setFiles(null);
      setProblem(error instanceof ApiError
        ? { type: error.type, detail: error.detail }
        : { type: "drive_unreachable", detail: m.research.driveErrorOf(DRIVE_LABEL[from]) });
    } finally {
      setLoading(false);
    }
  }, []);

  // which drives are connected, then the first connected one's files
  useEffect(() => {
    let live = true;
    void (async () => {
      let page: unknown = null;
      try { page = await api<MailAndDrive>("GET", "/v1/mail-and-drive"); } catch { /* unknown: the server answers per drive */ }
      if (!live) return;
      const on = enabledSources(page);
      const first = DRIVE_SOURCES.find((s) => on.has(s));
      setEnabled(on);
      if (first) {
        setSource(first);
        await load(first, "");
      } else {
        setLoading(false);
      }
    })();
    return () => { live = false; };
  }, [load]);

  const choose = (next: DriveSource) => {
    if (next === source || !enabled?.has(next)) return;
    setSource(next);
    setQuery("");
    setFiles(null);
    void load(next, "");
  };

  const toggle = (id: string) => setChosen((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const add = () => {
    const take = (files ?? []).filter((f) => chosen.has(f.id) && !picked.has(f.id));
    if (!take.length) return;
    onPick(take, source);
    setChosen(new Set());
  };

  const off = DRIVE_SOURCES.filter((s) => enabled && !enabled.has(s));
  const count = (files ?? []).filter((f) => chosen.has(f.id) && !picked.has(f.id)).length;
  const stopped = problem && STOP.has(problem.type);
  const none = enabled !== null && enabled.size === 0;

  return (
    <section className="card quiet pad-sm stack-8" aria-label={m.research.driveTitle}>
      <div className="seg" role="group" aria-label={m.research.driveSource}>
        {DRIVE_SOURCES.map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={!none && s === source}
            disabled={enabled === null || !enabled.has(s)}
            onClick={() => choose(s)}
          >
            {DRIVE_LABEL[s]}
          </button>
        ))}
      </div>
      {off.map((s) => (
        <p key={s} className="small muted">
          {m.research.driveNotConnected(DRIVE_LABEL[s])}{" "}
          <Link className="link" href="/mail-and-drive" prefetch={false} aria-label={`${m.research.driveConnectHere}: ${DRIVE_LABEL[s]}`}>
            {m.research.driveConnectHere}
          </Link>
        </p>
      ))}
      {none ? null : stopped ? (
        <div className="stack-8" role="status">
          <p className="small">{problem.detail}</p>
          {CONNECT.has(problem.type) ? (
            <div><Link className="btn primary sm" href="/mail-and-drive" prefetch={false}>{CONNECT_LINK[source]}</Link></div>
          ) : null}
        </div>
      ) : (
        <>
          <form
            className="row-12 wrap"
            role="search"
            onSubmit={(e) => { e.preventDefault(); void load(source, query); }}
          >
            <input
              className="input sm grow-240"
              type="search"
              value={query}
              maxLength={100}
              placeholder={m.research.driveSearch(label)}
              aria-label={m.research.driveSearch(label)}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button type="submit" className="btn sm" disabled={loading || enabled === null}>Search</button>
          </form>
          {problem ? <Notice kind="error">{problem.detail}</Notice> : null}
          {loading ? <p className="small muted" role="status">{m.research.driveLoading(label)}</p> : null}
          {!loading && files && !files.length ? (
            <p className="small muted">{source === "onedrive" ? m.research.driveEmptyOneDrive : m.research.driveEmpty}</p>
          ) : null}
          {!loading && files?.length ? (
            <ul className="rows uploads" aria-label={`${label}: ${m.research.driveTitle}`}>
              {files.map((f) => {
                const added = picked.has(f.id);
                return (
                  <li key={f.id}>
                    <label className="check grow">
                      <input
                        type="checkbox"
                        checked={added || chosen.has(f.id)}
                        disabled={disabled || added}
                        aria-label={m.research.driveSelect(f.name)}
                        onChange={() => toggle(f.id)}
                      />
                      <span className="grow">
                        <span className="strong ink">{cellText(f.name)}</span>
                        <span className="sub">
                          {f.google_native ? `Google file, added as ${f.type.toUpperCase()}` : f.type.toUpperCase()}
                          {f.size_bytes !== null ? `. ${fileSize(f.size_bytes)}` : ""}
                          {f.modified_at ? `. Modified ${when(f.modified_at)}` : ""}
                        </span>
                      </span>
                    </label>
                    {added ? <Status tone="ok">{m.research.driveAdded}</Status> : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
          {!loading && files?.length ? (
            <div className="actions">
              <button type="button" className="btn primary sm" disabled={disabled || !count} onClick={add}>
                {m.research.driveAdd(count)}
              </button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
