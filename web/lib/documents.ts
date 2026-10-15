// Uploaded documents for "From a file" research: pure helpers for file checks, per-file stage
// text, readiness and the detected company. The backend decides every status, verdict and cost;
// these only choose words and what the screen may offer next.
import type { DocumentStatus } from "@/lib/gen/api.gen";
import type { StatusTone } from "@/ui/primitives";

// accepted extensions (the server checks the content again; this only avoids a wasted upload)
// .zip: a zip of these, read member by member on the server (owner request 2026-10-15)
export const ACCEPT = [".pdf", ".docx", ".xlsx", ".csv", ".pptx", ".txt", ".md", ".zip"];
export const AUDIO = [".mp3", ".wav", ".m4a"];
// no size or count limit here: the server checks each file against the admin's per-file limit
// (owner decision 2026-10-14: files of any size, 20 GB by default)

export type Stage = "waiting" | "uploading" | "scanning" | "parsing" | "parsed" | "indexing" | "indexed" | "failed";

export const STAGE_TEXT: Record<Stage, string> = {
  waiting: "Waiting",
  uploading: "Uploading",
  scanning: "Scanning",
  parsing: "Reading",
  parsed: "Read, waiting for the company",
  indexing: "Reading",
  indexed: "Ready",
  failed: "Failed",
};

export function stageTone(stage: Stage): StatusTone {
  if (stage === "indexed") return "ok";
  if (stage === "failed") return "bad";
  if (stage === "parsed") return "warn";
  return "neutral";
}

function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

export function fileSize(bytes: number): string {
  if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(1)} GB`;
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/** Why a picked file cannot be uploaded (null: it may be sent). */
export function refuseReason(name: string, size: number): string | null {
  const ext = extension(name);
  if (AUDIO.includes(ext)) return "Audio transcription is not set up yet. Upload a transcript instead.";
  if (!ACCEPT.includes(ext)) return "This file type is not supported. Use PDF, Word, Excel, CSV, PowerPoint, text, Markdown or a zip of them.";
  if (size <= 0) return "The file is empty.";
  return null;
}

/** Files picked from a folder: the ones to upload and the ones left out with why (hidden and
 * system files are left out silently; a folder upload never lists every .DS_Store). */
export function folderFiles(files: ArrayLike<File>): { send: File[]; skipped: string[] } {
  const send: File[] = [];
  const skipped: string[] = [];
  for (const file of Array.from(files)) {
    const path = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
    const parts = path.split("/");
    if (parts.some((p) => p.startsWith(".") || p === "__MACOSX" || p === "Thumbs.db" || p === "desktop.ini")) continue;
    if (refuseReason(file.name, file.size)) skipped.push(path);
    else send.push(file);
  }
  return { send, skipped };
}

/** "Reading part 3 of 12" while a large file is read in parts (null for a one-part file). */
export function partsLine(doc: Pick<DocumentStatus, "stage" | "parts_total" | "parts_done">): string | null {
  if (doc.parts_total <= 1 || doc.stage !== "parsing") return null;
  return `Reading part ${Math.min(doc.parts_total, doc.parts_done + 1)} of ${doc.parts_total}`;
}

/** A stage still moving on the server (keep polling). */
export function isWorking(stage: Stage): boolean {
  return stage === "uploading" || stage === "scanning" || stage === "parsing" || stage === "indexing";
}

/** The stage to show and poll: a file bound to its company but not yet picked up for indexing
 * still reports "parsed" for a moment; it is on its way to Ready, not waiting (2026-10-15). */
export function stageOf(doc: Pick<DocumentStatus, "stage" | "needs_company">): Stage {
  return doc.stage === "parsed" && !doc.needs_company ? "indexing" : (doc.stage as Stage);
}

/** Every document is indexed: the research may start. */
export function allIndexed(docs: Pick<DocumentStatus, "stage">[]): boolean {
  return docs.length > 0 && docs.every((d) => d.stage === "indexed");
}

/** Documents read but not yet tied to a company (the confirm step is needed). */
export function needingCompany(docs: DocumentStatus[]): DocumentStatus[] {
  return docs.filter((d) => d.needs_company);
}

export interface CompanyCandidate { name: string | null; domain: string | null; score?: number; mentions?: number }

interface Detection { status?: string; company?: CompanyCandidate | null; candidates?: CompanyCandidate[] }

/** The detected company across documents: resolved only when they agree; never a guess. */
export function detectedCompany(docs: DocumentStatus[]): { status: "resolved" | "ambiguous" | "none"; company: CompanyCandidate | null; candidates: CompanyCandidate[] } {
  const found = docs.map((d) => (d.company ?? null) as Detection | null).filter((c): c is Detection => c !== null);
  const resolved = found.filter((c) => c.status === "resolved" && c.company?.domain);
  const domains = new Set(resolved.map((c) => c.company?.domain));
  const candidates: CompanyCandidate[] = [];
  const seen = new Set<string>();
  for (const c of found) {
    for (const cand of c.candidates ?? []) {
      const key = cand.domain ?? cand.name ?? "";
      if (key && !seen.has(key)) {
        seen.add(key);
        candidates.push({ name: cand.name, domain: cand.domain, score: cand.score, mentions: cand.mentions });
      }
    }
  }
  if (found.some((c) => c.status === "ambiguous") || domains.size > 1) return { status: "ambiguous", company: null, candidates };
  if (domains.size === 1) return { status: "resolved", company: resolved[0]?.company ?? null, candidates };
  return { status: "none", company: null, candidates };
}

export const DOMAIN_RE = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$/;

/** A typed domain in the shape the server accepts (lower case, no scheme, no www). */
export function cleanDomain(raw: string): string {
  return raw.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/.*$/, "");
}

export function usd(value: number): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: value < 1 ? 4 : 2 }).format(value);
}
