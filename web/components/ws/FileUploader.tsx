"use client";
// Chunked, resumable upload of one research file of any type and size. The file is sliced in the
// browser (never read whole), each part is sent with up to three tries, and the upload id is kept
// in this browser so a reload or a dropped connection resumes with only the missing parts.
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { UploadCloud } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { fileSize, missingParts, resumeKey } from "@/lib/research-files";
import type { FileUploadCompleted, FileUploadStarted, FileUploadState } from "@/lib/gen/api.gen";

type Phase = { kind: "idle" } | { kind: "sending"; done: number; total: number; bytes: number } | { kind: "finishing" } | { kind: "error"; text: string };

function remember(key: string, uploadId: string | null): void {
  try {
    if (uploadId) localStorage.setItem(key, uploadId);
    else localStorage.removeItem(key);
  } catch {
    // storage unavailable (private window): the upload still works, it just cannot resume
  }
}

function recall(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function FileUploader({ onDone }: { onDone?: (fileId: string) => void }) {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [picked, setPicked] = useState<File | null>(null);
  const abort = useRef<AbortController | null>(null);

  async function sendPart(uploadId: string, n: number, blob: Blob, signal: AbortSignal): Promise<void> {
    let last: unknown = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        await api("PUT", `/v1/files/uploads/${uploadId}/parts/${n}`, blob, { "Content-Type": "application/octet-stream" }, signal);
        return;
      } catch (error) {
        if (signal.aborted) throw error;
        if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 409) throw error;
        last = error;
        await new Promise((r) => setTimeout(r, 800 * (attempt + 1)));
      }
    }
    throw last;
  }

  async function start(file: File) {
    const controller = new AbortController();
    abort.current = controller;
    const key = resumeKey(file);
    try {
      let plan: { uploadId: string; partSize: number; total: number; received: number[] } | null = null;
      const saved = recall(key);
      if (saved) {
        try {
          const state = await api<FileUploadState>("GET", `/v1/files/uploads/${saved}`);
          if (state.status === "open") plan = { uploadId: saved, partSize: state.part_size, total: state.parts_total, received: state.received };
          else if (state.file_id) {
            remember(key, null);
            finish(state.file_id);
            return;
          }
        } catch {
          remember(key, null);
        }
      }
      if (!plan) {
        const opened = await api<FileUploadStarted>("POST", "/v1/files/uploads", { name: file.name, size: file.size, mime: file.type || null });
        plan = { uploadId: opened.upload_id, partSize: opened.part_size, total: opened.parts_total, received: [] };
        remember(key, opened.upload_id);
      }
      const todo = missingParts(plan.total, plan.received);
      let done = plan.total - todo.length;
      setPhase({ kind: "sending", done, total: plan.total, bytes: Math.min(file.size, done * plan.partSize) });
      for (const n of todo) {
        const blob = file.slice(n * plan.partSize, Math.min(file.size, (n + 1) * plan.partSize));
        await sendPart(plan.uploadId, n, blob, controller.signal);
        done += 1;
        setPhase({ kind: "sending", done, total: plan.total, bytes: Math.min(file.size, done * plan.partSize) });
      }
      setPhase({ kind: "finishing" });
      const completed = await api<FileUploadCompleted>("POST", `/v1/files/uploads/${plan.uploadId}/complete`);
      remember(key, null);
      finish(completed.file_id);
    } catch (error) {
      if (controller.signal.aborted) {
        setPhase({ kind: "error", text: "Upload paused. Choose the same file again to continue where it stopped." });
        return;
      }
      const text = error instanceof ApiError && error.status === 413
        ? "This file is larger than the upload limit."
        : "The upload stopped. Choose the same file again to continue where it stopped.";
      setPhase({ kind: "error", text });
    } finally {
      abort.current = null;
    }
  }

  function finish(fileId: string) {
    setPhase({ kind: "idle" });
    setPicked(null);
    if (onDone) onDone(fileId);
    router.push(`/research-files/${encodeURIComponent(fileId)}`);
  }

  const sending = phase.kind === "sending" || phase.kind === "finishing";
  const percent = phase.kind === "sending" ? Math.round((phase.done / Math.max(phase.total, 1)) * 100) : phase.kind === "finishing" ? 100 : 0;
  return (
    <div className="card stack">
      <label className="field" htmlFor="research-file">
        <span className="strong">Choose a file</span>
        <span className="hint">Any type and size: spreadsheets, PDFs, documents, slides, text, images, recordings or archives. Large files upload in parts and can be resumed.</span>
      </label>
      <input
        id="research-file"
        type="file"
        className="input"
        disabled={sending}
        onChange={(e) => { setPicked(e.target.files?.[0] ?? null); setPhase({ kind: "idle" }); }}
      />
      {picked ? <p className="ink-2 t-footnote">{picked.name} · {fileSize(picked.size)}</p> : null}
      {phase.kind === "sending" || phase.kind === "finishing" ? (
        <div className="stack" aria-live="polite">
          <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label="Upload progress">
            <div className="progress-fill" style={{ width: `${percent}%` }} />
          </div>
          <p className="ink-2 t-footnote">
            {phase.kind === "finishing" ? "Checking the file…" : `Part ${phase.done} of ${phase.total} · ${fileSize(phase.bytes)} sent`}
          </p>
        </div>
      ) : null}
      {phase.kind === "error" ? <p className="inline-error" role="alert">{phase.text}</p> : null}
      <div className="row">
        <button type="button" className="btn primary" disabled={!picked || sending} onClick={() => picked && void start(picked)}>
          <UploadCloud size={16} strokeWidth={1.6} aria-hidden="true" /> Upload
        </button>
        {sending ? <button type="button" className="btn quiet" onClick={() => abort.current?.abort()}>Pause</button> : null}
      </div>
    </div>
  );
}
