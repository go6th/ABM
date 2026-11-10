"use client";
// Add the files a "From a file" research reads (owner decision 2026-10-14): drop them or "Add
// files". Each file goes straight to storage in resumable chunks (the
// bar shows the bytes sent; a reload keeps the place: choose the same file again to continue),
// is completed, then followed through scanning and reading. A large file is read in parts
// ("Reading part 3 of 12"); a part that could not be read shows its reason and "Try again".
// Removing a saved file, or a paused upload, asks first and says if it failed; while a file is
// still being sent the tab warns before it is closed or reloaded.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, errorText } from "@/lib/api";
import { m } from "@/lib/messages";
import { ACCEPT, STAGE_TEXT, fileSize, folderFiles, isWorking, progressLine, refuseReason, stageOf, stageTone, type Stage } from "@/lib/documents";
import { uploadDocument } from "@/lib/upload";
import { forgetPaused, pausedUploads, type Saved } from "@/lib/upload-store";
import { ConfirmDialog } from "@/ui/Modal";
import { announce } from "@/ui/Toaster";
import { Status } from "@/ui/primitives";
import { cellText } from "@/lib/render";
import type { DocumentStatus } from "@/lib/gen/api.gen";
import { AccountPicker } from "./AccountPicker";
import { WorkspaceCompanyPrompt, workspaceOf } from "./WorkspaceCompanyPrompt";

export interface DropItem { key: string; name: string; size: number; stage: Stage; message: string | null; doc: DocumentStatus | null; sent: number }

const POLL_MS = 2000;

export function DocumentDrop({ accountId = null, onChange, onSending, disabled = false, refresh = 0, picker = true }: {
  accountId?: string | null;
  /** told whether any file is still being sent (a pop-up around the drop stays open meanwhile) */
  onSending?: (sending: boolean) => void;
  // show the account picker under a file the server holds as "Needs account" (a new research
  // confirms the company in its own panel instead)
  picker?: boolean;
  onChange: (docs: DocumentStatus[]) => void;
  disabled?: boolean;
  // bump to read every file's status again (after the company is confirmed the server indexes
  // files that sat at "parsed", which the poll below had stopped watching)
  refresh?: number;
}) {
  const [items, setItems] = useState<DropItem[]>([]);
  const [paused, setPaused] = useState<Saved[]>([]);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement | null>(null);
  const folder = useRef<HTMLInputElement | null>(null);
  const [skipped, setSkipped] = useState<string[]>([]);
  const counter = useRef(0);
  const aborts = useRef(new Map<string, AbortController>());
  // the saved file (or paused upload) a person asked to remove, waiting for their answer
  const [asking, setAsking] = useState<{ item: DropItem } | { saved: Saved } | null>(null);
  // part retries in flight ("<item key>:<part>"), so a double click posts once
  const [retrying, setRetrying] = useState<string[]>([]);
  const retryingNow = useRef(new Set<string>());

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

  // the status poll watches only whether anything is still being read: ``items`` changes on every
  // progress event, so a timer rebuilt on each change never got to fire during an upload
  const itemsRef = useRef(items);
  useEffect(() => { itemsRef.current = items; }, [items]); // runs before the refresh effect below
  const hasWorking = items.some((i) => i.doc && isWorking(i.stage));
  const sending = items.some((i) => i.stage === "uploading");
  useEffect(() => {
    if (!hasWorking) return;
    const timer = setInterval(() => {
      const working = itemsRef.current.filter((i) => i.doc && isWorking(i.stage));
      void Promise.all(working.map(async (it) => {
        try {
          const doc = await api<DocumentStatus>("GET", `/v1/documents/${it.doc!.document_id}/status`);
          update(it.key, { doc, stage: stageOf(doc), message: doc.message ?? null });
        } catch { /* the next poll tries again */ }
      }));
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [hasWorking, update]);

  useEffect(() => { onSending?.(sending); }, [sending, onSending]);

  // closing or reloading the tab while a file is still being sent would lose the place
  useEffect(() => {
    if (!sending) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [sending]);

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
            ? errorText(error, "The upload stopped. Choose the same file again to continue where it stopped.")
            : "The upload stopped. Choose the same file again to continue where it stopped.";
        update(key, { stage: "failed", message });
      } finally {
        aborts.current.delete(key);
      }
    }
  }, [accountId, update]);

  // remove one row: a file the server holds is deleted there after a yes (the pop-up below); a
  // file still going up, or one that never got saved, only stops
  const remove = (it: DropItem) => {
    if (it.doc) { setAsking({ item: it }); return; }
    aborts.current.get(it.key)?.abort();
    setItems((prev) => prev.filter((p) => p.key !== it.key));
  };

  const deleteSaved = async (documentId: string) => {
    try {
      await api("DELETE", `/v1/documents/${documentId}`);
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) { // 404: already gone
        throw new Error(errorText(error, "It could not be removed. Try again in a moment."));
      }
    }
  };

  const confirmRemove = async () => {
    if (!asking) return;
    if ("item" in asking) {
      const it = asking.item;
      aborts.current.get(it.key)?.abort();
      await deleteSaved(it.doc!.document_id);
      setItems((prev) => prev.filter((p) => p.key !== it.key));
    } else {
      await deleteSaved(asking.saved.documentId);
      forgetPaused(asking.saved);
      setPaused((prev) => prev.filter((p) => p.documentId !== asking.saved.documentId));
    }
    announce("File removed");
  };

  const retry = async (it: DropItem, part: number) => {
    const id = `${it.key}:${part}`;
    if (!it.doc || retryingNow.current.has(id)) return;
    retryingNow.current.add(id);
    setRetrying((prev) => [...prev, id]);
    try {
      const doc = await api<DocumentStatus>("POST", `/v1/documents/${it.doc.document_id}/parts/${part}/retry`);
      update(it.key, { doc, stage: stageOf(doc), message: doc.message ?? null });
    } catch (error) {
      update(it.key, { message: errorText(error, "The part could not be queued. Try again.") });
    } finally {
      retryingNow.current.delete(id);
      setRetrying((prev) => prev.filter((p) => p !== id));
    }
  };

  const reload = async (it: DropItem) => {
    if (!it.doc) return;
    try {
      const doc = await api<DocumentStatus>("GET", `/v1/documents/${it.doc.document_id}/status`);
      update(it.key, { doc, stage: stageOf(doc), message: doc.message ?? null });
    } catch { /* the next refresh tries again */ }
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
          <button type="button" className="btn" disabled={disabled} onClick={() => folder.current?.click()}>{m.research.chooseFolder}</button>
        </div>
        <input ref={input} type="file" multiple accept={ACCEPT.join(",")} aria-label={m.research.chooseFiles} onChange={(e) => { if (e.target.files) void add(e.target.files); e.target.value = ""; }} />
        {/* a whole folder, subfolders included; unsupported files are listed, not uploaded */}
        <input
          ref={folder}
          type="file"
          multiple
          aria-label={m.research.chooseFolder}
          {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
          onChange={(e) => {
            if (e.target.files) {
              const { send, skipped: left } = folderFiles(e.target.files);
              setSkipped(left);
              if (send.length) void add(send);
            }
            e.target.value = "";
          }}
        />
      </div>
      {skipped.length ? (
        <p className="small muted" role="status">
          {skipped.length} file{skipped.length === 1 ? " was" : "s were"} left out of the folder because their type is not supported: {skipped.slice(0, 5).join(", ")}{skipped.length > 5 ? ` and ${skipped.length - 5} more` : ""}.
        </p>
      ) : null}
      {paused.length ? (
        <ul className="rows uploads" aria-label="Paused uploads">
          {paused.map((p) => (
            <li key={p.documentId}>
              <div>
                <p>{cellText(p.name)}</p>
                <span className="sub">Paused at {fileSize(p.sent)} of {fileSize(p.size)}. Choose the same file again to continue.</span>
              </div>
              <button type="button" className="link-btn small" onClick={() => setAsking({ saved: p })} disabled={disabled}>{m.research.remove}</button>
            </li>
          ))}
        </ul>
      ) : null}
      {items.length ? (
        <ul className="rows uploads" aria-label={m.research.filesLabel}>
          {items.map((it) => {
            const reading = it.doc ? progressLine(it.doc) : null;
            return (
              <li key={it.key} className="file-row">
                <div className="file-head">
                  <div>
                    <p>{cellText(it.name)}</p>
                    <span className="sub">
                      {it.stage === "uploading" ? `${fileSize(it.sent)} of ${fileSize(it.size)} sent` : fileSize(it.size)}
                      {reading ? `. ${reading}` : ""}
                      {it.doc?.passages ? `. ${it.doc.passages.toLocaleString()} sections read` : ""}
                      {it.message ? `. ${it.message}` : ""}
                    </span>
                  </div>
                  <div className="row nowrap">
                    <span aria-live="polite"><Status tone={stageTone(it.stage)}>{STAGE_TEXT[it.stage]}</Status></span>
                    <button type="button" className="link-btn small" onClick={() => remove(it)} disabled={disabled}>{m.research.remove}</button>
                  </div>
                </div>
                {it.stage === "uploading" ? (
                  <progress className="upload-bar" max={Math.max(it.size, 1)} value={it.sent} aria-label={`Upload of ${it.name}`} />
                ) : it.doc && it.doc.parts_total > 1 && it.stage === "parsing" ? (
                  <progress className="upload-bar" max={it.doc.parts_total} value={it.doc.parts_done} aria-label={`Reading ${it.name}`} />
                ) : null}
                {picker && it.doc && it.stage === "needs_account" ? (
                  workspaceOf(it.doc) ? (
                    <WorkspaceCompanyPrompt doc={it.doc} ws={workspaceOf(it.doc)!} onDone={() => void reload(it)} />
                  ) : (
                    <AccountPicker doc={it.doc} disabled={disabled} onConfirmed={() => void reload(it)} />
                  )
                ) : null}
                {it.doc?.parts_failed.length ? (
                  <ul className="part-fails" aria-label={`Parts of ${it.name} that could not be read`}>
                    {it.doc.parts_failed.map((f) => (
                      <li key={f.part}>
                        <span className="small">Part {f.part}: {f.reason}</span>
                        <button type="button" className="link-btn small" disabled={disabled} aria-busy={retrying.includes(`${it.key}:${f.part}`) || undefined} onClick={() => void retry(it, f.part)}>Try again</button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {asking ? (
        <ConfirmDialog
          title="Remove this file?"
          body={<>Remove <b>{cellText("item" in asking ? asking.item.name : asking.saved.name)}</b>? It is deleted from your workspace.</>}
          confirm="Remove"
          onConfirm={confirmRemove}
          onClose={() => setAsking(null)}
        />
      ) : null}
    </div>
  );
}
