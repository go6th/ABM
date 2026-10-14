// Files go from the browser straight to object storage (owner decision 2026-10-14): never
// through this web app or the API. The API opens a resumable session; the file is sent in chunks
// (each retried on its own); a reload or a dropped connection asks storage how much it has and
// continues from there. Progress counts the bytes storage has confirmed, plus the chunk in flight.
// The session URL is kept in this browser so the same file, chosen again, resumes.
import { api, ApiError } from "@/lib/api";
import type { DocumentStatus, DocumentUploadStarted } from "@/lib/gen/api.gen";
import { readSaved, uploadKey, writeSaved, type Saved } from "@/lib/upload-store";

type Progress = (sent: number, total: number) => void;

const TRIES = 5;

class Expired extends Error {}

/** The committed byte count from a 308's ``Range: bytes=0-N`` (0 when absent). */
export function committedFrom(range: string | null): number {
  const match = /bytes=0-(\d+)/.exec(range ?? "");
  return match ? Number(match[1]) + 1 : 0;
}

function put(url: string, range: string, body: Blob | null, signal: AbortSignal, onSent?: (n: number) => void): Promise<{ status: number; range: string | null }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Range", range);
    if (onSent) xhr.upload.onprogress = (event) => onSent(event.loaded);
    xhr.onload = () => resolve({ status: xhr.status, range: xhr.getResponseHeader("Range") });
    xhr.onerror = () => reject(new TypeError("network"));
    xhr.onabort = () => reject(new DOMException("aborted", "AbortError"));
    signal.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(body);
  });
}

async function pause(attempt: number, signal: AbortSignal): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, Math.min(15000, 600 * 2 ** attempt));
    signal.addEventListener("abort", () => { clearTimeout(timer); reject(new DOMException("aborted", "AbortError")); }, { once: true });
  });
}

/** How many bytes storage holds (the file's size once it is complete). */
async function committed(url: string, total: number, signal: AbortSignal): Promise<number> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      const reply = await put(url, `bytes */${total}`, null, signal);
      if (reply.status === 200 || reply.status === 201) return total;
      if (reply.status === 308) return committedFrom(reply.range);
      if (reply.status === 404 || reply.status === 410) throw new Expired();
      if (reply.status < 500 && reply.status !== 429) throw new ApiError(reply.status, "upload_failed", "The file could not be stored.");
    } catch (error) {
      if (signal.aborted || error instanceof Expired || error instanceof ApiError || attempt + 1 >= TRIES) throw error;
    }
    await pause(attempt, signal);
  }
}

async function sendChunks(saved: Saved, file: File, onProgress: Progress, signal: AbortSignal, remember: (sent: number) => void): Promise<void> {
  let offset = await committed(saved.url, file.size, signal);
  onProgress(offset, file.size);
  // LOOP(upload chunks): cap=size / chunk chunks, TRIES per chunk; exit=storage has every byte
  while (offset < file.size) {
    const end = Math.min(file.size, offset + saved.chunk);
    let moved = false;
    for (let attempt = 0; attempt < TRIES && !moved; attempt += 1) {
      try {
        const reply = await put(saved.url, `bytes ${offset}-${end - 1}/${file.size}`, file.slice(offset, end), signal, (n) => onProgress(offset + n, file.size));
        if (reply.status === 200 || reply.status === 201) { offset = file.size; moved = true; break; }
        if (reply.status === 308) { offset = committedFrom(reply.range); moved = true; break; }
        if (reply.status === 404 || reply.status === 410) throw new Expired();
        if (reply.status < 500 && reply.status !== 429) throw new ApiError(reply.status, "upload_failed", "The file could not be stored.");
      } catch (error) {
        if (signal.aborted || error instanceof Expired || error instanceof ApiError) throw error;
      }
      await pause(attempt, signal);
      offset = await committed(saved.url, file.size, signal); // what arrived before the drop
      moved = offset >= end;
    }
    if (!moved) throw new TypeError("network");
    remember(offset);
    onProgress(offset, file.size);
  }
}

/** Send one file and complete it; resumes an upload this browser started for the same file. */
export async function uploadDocument(file: File, accountId: string | null, onProgress: Progress, signal: AbortSignal): Promise<string> {
  const key = uploadKey(file);
  let saved = readSaved(key);
  if (saved && new Date(saved.expires).getTime() <= Date.now()) saved = null;
  for (let round = 0; round < 2; round += 1) {
    let current: Saved;
    if (saved) {
      current = saved;
    } else {
      const started = await api<DocumentUploadStarted>("POST", "/v1/documents/uploads", {
        name: file.name, size_bytes: file.size, mime: file.type || null, account_id: accountId,
      });
      current = {
        documentId: started.document_id, name: file.name, size: file.size, url: started.upload_url,
        protocol: started.protocol === "resumable" ? "resumable" : "single", chunk: started.chunk_bytes,
        expires: started.expires_at, sent: 0,
      };
      writeSaved(key, current);
    }
    try {
      if (current.protocol === "resumable") {
        await sendChunks(current, file, onProgress, signal, (sent) => writeSaved(key, { ...current, sent }));
      } else {
        await api("PUT", current.url, file, { "Content-Type": "application/octet-stream" }, signal);
        onProgress(file.size, file.size);
      }
      await api<DocumentStatus>("POST", `/v1/documents/${current.documentId}/complete`);
      writeSaved(key, null);
      return current.documentId;
    } catch (error) {
      if (!(error instanceof Expired)) throw error;
      writeSaved(key, null); // the session ran out: start again from the first byte
      saved = null;
    }
  }
  throw new ApiError(410, "upload_expired", "The upload expired. Choose the file again.");
}
