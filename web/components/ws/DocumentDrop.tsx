"use client";
// Drop or pick the files a "From a file" research reads, or choose from the workspace storage
// list. Each file is checked here first (type, size), declared to the API, sent to storage,
// completed, then followed through scanning, reading and indexing. A failed file shows the
// server's reason and can be removed.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { m } from "@/lib/messages";
import { ACCEPT, MAX_FILES, MAX_FILE_BYTES, STAGE_TEXT, fileSize, isWorking, refuseReason, stageTone, type Stage } from "@/lib/documents";
import { Status } from "@/ui/primitives";
import { cellText } from "@/lib/render";
import type { DocumentList, DocumentStatus, DocumentUploadStarted } from "@/lib/gen/api.gen";

export interface DropItem { key: string; name: string; size: number; stage: Stage; message: string | null; doc: DocumentStatus | null }

const POLL_MS = 1500;

async function send(file: File, accountId: string | null): Promise<string> {
  const started = await api<DocumentUploadStarted>("POST", "/v1/documents/uploads", {
    name: file.name, size_bytes: file.size, mime: file.type || null, account_id: accountId,
  });
  if (started.direct) {
    const put = await fetch(started.upload_url, { method: "PUT", body: file, headers: started.headers });
    if (!put.ok) throw new ApiError(put.status, "upload_failed", "The file could not be stored.");
  } else {
    await api("PUT", started.upload_url, file, { "Content-Type": "application/octet-stream" });
  }
  await api<DocumentStatus>("POST", `/v1/documents/${started.document_id}/complete`);
  return started.document_id;
}

export function DocumentDrop({ accountId = null, onChange, disabled = false }: {
  accountId?: string | null;
  onChange: (docs: DocumentStatus[]) => void;
  disabled?: boolean;
}) {
  const [items, setItems] = useState<DropItem[]>([]);
  const [over, setOver] = useState(false);
  const [storage, setStorage] = useState<DocumentStatus[] | null>(null);
  const [showStorage, setShowStorage] = useState(false);
  const input = useRef<HTMLInputElement | null>(null);
  const counter = useRef(0);

  const update = useCallback((key: string, patch: Partial<DropItem>) => {
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...patch } : it)));
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
          update(it.key, { doc, stage: doc.stage as Stage, message: doc.message ?? null });
        } catch { /* the next poll tries again */ }
      }));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [items, update]);

  const add = useCallback(async (files: FileList | File[]) => {
    const list = Array.from(files).slice(0, Math.max(0, MAX_FILES - items.length));
    for (const file of list) {
      counter.current += 1;
      const key = `${counter.current}-${file.name}`;
      const refused = refuseReason(file.name, file.size, MAX_FILE_BYTES);
      setItems((prev) => [...prev, { key, name: file.name, size: file.size, stage: refused ? "failed" : "uploading", message: refused, doc: null }]);
      if (refused) continue;
      try {
        const id = await send(file, accountId);
        const doc = await api<DocumentStatus>("GET", `/v1/documents/${id}/status`);
        update(key, { doc, stage: doc.stage as Stage, message: doc.message ?? null });
      } catch (error) {
        update(key, { stage: "failed", message: error instanceof ApiError ? error.detail : "The upload did not finish. Try again." });
      }
    }
  }, [accountId, items.length, update]);

  const openStorage = async () => {
    setShowStorage((v) => !v);
    if (storage) return;
    try {
      const list = await api<DocumentList>("GET", "/v1/documents");
      setStorage(list.documents);
    } catch {
      setStorage([]);
    }
  };

  const pick = (doc: DocumentStatus) => {
    if (items.some((i) => i.doc?.document_id === doc.document_id)) return;
    setItems((prev) => [...prev, { key: `s-${doc.document_id}`, name: doc.name, size: doc.size_bytes, stage: doc.stage as Stage, message: doc.message ?? null, doc }]);
  };

  const remove = async (it: DropItem) => {
    if (it.doc && !it.key.startsWith("s-")) {
      try { await api("DELETE", `/v1/documents/${it.doc.document_id}`); } catch { /* already gone */ }
    }
    setItems((prev) => prev.filter((p) => p.key !== it.key));
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
          <button type="button" className="btn" disabled={disabled} aria-expanded={showStorage} onClick={() => void openStorage()}>{m.research.fromStorage}</button>
        </div>
        <input ref={input} type="file" multiple accept={ACCEPT.join(",")} aria-label={m.research.chooseFiles} onChange={(e) => { if (e.target.files) void add(e.target.files); e.target.value = ""; }} />
      </div>
      {showStorage ? (
        storage === null ? <p className="loading small"><span className="spinner" aria-hidden="true" />{m.state.loading}</p>
          : storage.length === 0 ? <p className="small muted">No files in storage yet.</p>
            : (
              <ul className="rows" aria-label={m.research.fromStorage}>
                {storage.map((d) => (
                  <li key={d.document_id}>
                    <div><p>{cellText(d.name)}</p><span className="sub">{fileSize(d.size_bytes)}</span></div>
                    <button type="button" className="link-btn small" disabled={disabled || items.some((i) => i.doc?.document_id === d.document_id)} onClick={() => pick(d)}>Add</button>
                  </li>
                ))}
              </ul>
            )
      ) : null}
      {items.length ? (
        <ul className="rows" aria-label={m.research.filesLabel}>
          {items.map((it) => (
            <li key={it.key}>
              <div>
                <p>{cellText(it.name)}</p>
                <span className="sub">{fileSize(it.size)}{it.doc?.passages ? `. ${it.doc.passages} passages` : ""}{it.message ? `. ${it.message}` : ""}</span>
              </div>
              <div className="row nowrap">
                <span aria-live="polite"><Status tone={stageTone(it.stage)}>{STAGE_TEXT[it.stage]}</Status></span>
                <button type="button" className="link-btn small" onClick={() => void remove(it)} disabled={disabled}>{m.research.remove}</button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
