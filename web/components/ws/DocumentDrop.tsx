"use client";
// Add the files a "From a file" research reads (owner decision 2026-10-14): drop them or "Add
// files", or "Connect Google Drive" (DriveChooser: the server fetches the chosen files from the
// user's own Drive into the same path). Each file goes straight to storage in resumable chunks (the
// bar shows the bytes sent; a reload keeps the place: choose the same file again to continue),
// is completed, then followed through scanning and reading. A large file is read in parts
// ("Reading part 3 of 12"); a part that could not be read shows its reason and "Try again".
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { m } from "@/lib/messages";
import { ACCEPT, STAGE_TEXT, fileSize, isWorking, partsLine, refuseReason, stageOf, stageTone, type Stage } from "@/lib/documents";
import { uploadDocument } from "@/lib/upload";
import { forgetPaused, pausedUploads, type Saved } from "@/lib/upload-store";
import { Status } from "@/ui/primitives";
import { cellText } from "@/lib/render";
import type { DocumentStatus, DriveFile } from "@/lib/gen/api.gen";
import { DriveChooser } from "./DriveChooser";

export interface DropItem { key: string; name: string; size: number; stage: Stage; message: string | null; doc: DocumentStatus | null; sent: number; driveId?: string }

const POLL_MS = 2000;

export function DocumentDrop({ accountId = null, onChange, disabled = false, initialDrive = false, refresh = 0 }: {
  accountId?: string | null;
  onChange: (docs: DocumentStatus[]) => void;
  disabled?: boolean;
  initialDrive?: boolean;
  // bump to read every file's status again (after the company is confirmed the server indexes
  // files that sat at "parsed", which the poll below had stopped watching)
  refresh?: number;
}) {
  const [items, setItems] = useState<DropItem[]>([]);
  const [paused, setPaused] = useState<Saved[]>([]);
  const [over, setOver] = useState(false);
  const [drive, setDrive] = useState(initialDrive);
  const input = useRef<HTMLInputElement | null>(null);
  const counter = useRef(0);
  const aborts = useRef(new Map<string, AbortController>());

  const update = useCallback((key: string, patch: Partial<DropItem>) => {
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...patch } : it)));
  }, []);

  useEffect(() => {
    // read after mount (the server render has no browser storage)
    const timer = setTimeout(() => setPaused(pausedUploads()), 0);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    onChange(items.map((i) => i.doc).filter((d): d is DocumentStatus => d !== null));
  }, [items, onChange]);

  useEffect(() => {
    const working = items.filter((i) => i.doc && isWorking(i.stage));
    if (!working.length) return;
    const timer = setTimeout(() => {
      void Promise.all(working.map(async (it) => {
        try {
          const doc = await api<DocumentStatus>("GET", `/v1/documents/${it.doc!.document_id}/status`);
          update(it.key, { doc, stage: stageOf(doc), message: doc.message ?? null });
        } catch { /* the next poll tries again */ }
      }));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [items, update]);

  const itemsRef = useRef(items);
  useEffect(() => { itemsRef.current = items; }, [items]); // runs before the refresh effect below
  useEffect(() => {
    if (!refresh) return;
    const known = itemsRef.current.filter((i) => i.doc);
    void Promise.all(known.map(async (it) => {
      try {
        const doc = await api<DocumentStatus>("GET", `/v1/documents/${it.doc!.document_id}/status`);
        update(it.key, { doc, stage: stageOf(doc), message: doc.message ?? null });
      } catch { /* the next refresh or poll tries again */ }
    }));
  }, [refresh, update]);

  const add = useCallback(async (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      counter.current += 1;
      const key = `${counter.current}-${file.name}`;
      const refused = refuseReason(file.name, file.size);
      setItems((prev) => [...prev, { key, name: file.name, size: file.size, stage: refused ? "failed" : "uploading", message: refused, doc: null, sent: 0 }]);
      setPaused((prev) => prev.filter((p) => !(p.name === file.name && p.size === file.size)));
      if (refused) continue;
      const controller = new AbortController();
      aborts.current.set(key, controller);
      try {
        const id = await uploadDocument(file, accountId, (sent) => update(key, { sent }), controller.signal);
        const doc = await api<DocumentStatus>("GET", `/v1/documents/${id}/status`);
        update(key, { doc, stage: stageOf(doc), message: doc.message ?? null });
      } catch (error) {
        const message = controller.signal.aborted
          ? "Upload paused. Choose the same file again to continue where it stopped."
          : error instanceof ApiError && error.type !== "upload_failed"
            ? error.detail
            : "The upload stopped. Choose the same file again to continue where it stopped.";
        update(key, { stage: "failed", message });
      } finally {
        aborts.current.delete(key);
      }
    }
  }, [accountId, update]);

  const addFromDrive = useCallback(async (file: DriveFile) => {
    counter.current += 1;
    const key = `${counter.current}-drive-${file.id}`;
    setItems((prev) => [...prev, { key, name: file.name, size: file.size_bytes ?? 0, stage: "uploading", message: null, doc: null, sent: 0, driveId: file.id }]);
    try {
      const doc = await api<DocumentStatus>("POST", "/v1/documents/from-drive", { file_id: file.id, account_id: accountId });
      update(key, { doc, size: doc.size_bytes, stage: stageOf(doc), message: doc.message ?? null });
    } catch (error) {
      update(key, { stage: "failed", message: error instanceof ApiError ? error.detail : m.research.driveError });
    }
  }, [accountId, update]);

  const remove = async (it: DropItem) => {
    aborts.current.get(it.key)?.abort();
    if (it.doc) {
      try { await api("DELETE", `/v1/documents/${it.doc.document_id}`); } catch { /* already gone */ }
    }
    setItems((prev) => prev.filter((p) => p.key !== it.key));
  };

  const retry = async (it: DropItem, part: number) => {
    if (!it.doc) return;
    try {
      const doc = await api<DocumentStatus>("POST", `/v1/documents/${it.doc.document_id}/parts/${part}/retry`);
      update(it.key, { doc, stage: stageOf(doc), message: doc.message ?? null });
    } catch (error) {
      update(it.key, { message: error instanceof ApiError ? error.detail : "The part could not be queued. Try again." });
    }
  };

  const drop = (saved: Saved) => {
    forgetPaused(saved);
    setPaused((prev) => prev.filter((p) => p.documentId !== saved.documentId));
    void api("DELETE", `/v1/documents/${saved.documentId}`).catch(() => undefined);
  };

  return (
    <div className="stack">
      <div
        className="drop"
        data-over={over}
        onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); if (!disabled) void add(e.dataTransfer.files); }}
      >
        <p className="strong ink">{m.research.dropHere}</p>
        <p className="small">{m.research.fileTypes}</p>
        <div className="actions">
          <button type="button" className="btn" disabled={disabled} onClick={() => input.current?.click()}>{m.research.chooseFiles}</button>
          <button type="button" className="btn" disabled={disabled} aria-expanded={drive} onClick={() => setDrive((v) => !v)}>{m.research.connectDrive}</button>
        </div>
        <input ref={input} type="file" multiple accept={ACCEPT.join(",")} aria-label={m.research.chooseFiles} onChange={(e) => { if (e.target.files) void add(e.target.files); e.target.value = ""; }} />
      </div>
      {drive ? (
        <DriveChooser
          onPick={(f) => void addFromDrive(f)}
          picked={new Set(items.filter((i) => i.driveId && i.stage !== "failed").map((i) => i.driveId!))}
          disabled={disabled}
        />
      ) : null}
      {paused.length ? (
        <ul className="rows uploads" aria-label="Paused uploads">
          {paused.map((p) => (
            <li key={p.documentId}>
              <div>
                <p>{cellText(p.name)}</p>
                <span className="sub">Paused at {fileSize(p.sent)} of {fileSize(p.size)}. Choose the same file again to continue.</span>
              </div>
              <button type="button" className="link-btn small" onClick={() => drop(p)} disabled={disabled}>{m.research.remove}</button>
            </li>
          ))}
        </ul>
      ) : null}
      {items.length ? (
        <ul className="rows uploads" aria-label={m.research.filesLabel}>
          {items.map((it) => {
            const reading = it.doc ? partsLine(it.doc) : null;
            return (
              <li key={it.key} className="file-row">
                <div className="file-head">
                  <div>
                    <p>{cellText(it.name)}</p>
                    <span className="sub">
                      {it.stage === "uploading"
                        ? it.driveId ? m.research.driveFetching : `${fileSize(it.sent)} of ${fileSize(it.size)} sent`
                        : fileSize(it.size)}
                      {reading ? `. ${reading}` : ""}
                      {it.doc?.passages ? `. ${it.doc.passages.toLocaleString()} passages` : ""}
                      {it.message ? `. ${it.message}` : ""}
                    </span>
                  </div>
                  <div className="row nowrap">
                    <span aria-live="polite"><Status tone={stageTone(it.stage)}>{STAGE_TEXT[it.stage]}</Status></span>
                    <button type="button" className="link-btn small" onClick={() => void remove(it)} disabled={disabled}>{m.research.remove}</button>
                  </div>
                </div>
                {it.stage === "uploading" && !it.driveId ? (
                  <progress className="upload-bar" max={Math.max(it.size, 1)} value={it.sent} aria-label={`Upload of ${it.name}`} />
                ) : it.doc && it.doc.parts_total > 1 && it.stage === "parsing" ? (
                  <progress className="upload-bar" max={it.doc.parts_total} value={it.doc.parts_done} aria-label={`Reading ${it.name}`} />
                ) : null}
                {it.doc?.parts_failed.length ? (
                  <ul className="part-fails" aria-label={`Parts of ${it.name} that could not be read`}>
                    {it.doc.parts_failed.map((f) => (
                      <li key={f.part}>
                        <span className="small">Part {f.part}: {f.reason}</span>
                        <button type="button" className="link-btn small" disabled={disabled} onClick={() => void retry(it, f.part)}>Try again</button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
