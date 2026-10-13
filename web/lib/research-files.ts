// Research files: pure helpers for the upload plan, sizes, statuses, locations and chart sources.
// The backend decides every status, count and finding; these only choose words and layouts.

export const WORKING = new Set(["UPLOADED", "PARSING"]);

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export const STATUS_TEXT: Record<string, string> = {
  UPLOADED: "Uploaded, waiting to be read",
  PARSING: "Reading the file",
  AUDITED: "Audited, waiting for approval",
  ANALYZED: "Analysed",
  PARTIAL: "Partly analysed",
  FAILED: "Could not be read",
};

export function statusTone(status: string): "mint" | "butter" | "rose" | "peri" {
  if (status === "ANALYZED") return "mint";
  if (status === "FAILED") return "rose";
  if (status === "PARTIAL" || status === "AUDITED") return "butter";
  return "peri";
}

const REASONS: Record<string, string> = {
  unsupported: "This file type is stored but not read yet.",
  ocr_unavailable: "Text inside images is not read: no OCR engine is installed.",
  transcription_unavailable: "Recordings are stored but not transcribed: no speech model is configured.",
  truncated_or_corrupt: "Part of the file is damaged or cut off. Everything before the damage was read.",
  corrupt_file: "The file is damaged and could not be opened.",
  encrypted_file: "The file is password protected.",
  worker_lost: "Processing stopped unexpectedly. Everything read so far is kept; resume to continue.",
  model_credits_exhausted: "The language model account ran out of credits. Everything else is kept.",
  model_auth_failed: "The language model key was refused.",
  model_not_configured: "No language model is configured, so summaries are extracts.",
  model_refused: "The language model declined part of this file.",
  model_overloaded: "The language model was busy. Resume to try again; finished parts cost nothing.",
  approval_required: "The summary step needs your approval because of its estimated cost.",
  integrity_mismatch: "The stored file does not match the upload. Upload it again.",
  file_missing: "The stored file is missing. Upload it again.",
  zip_bomb_blocked: "The archive expands far beyond its size and was not unpacked.",
};

export function reasonText(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return REASONS[reason] ?? "Processing stopped before it finished. Everything read so far is kept.";
}

export type Location = Record<string, unknown>;

/** A short, human form of a location in the file. */
export function where(location: Location): string {
  const parts: string[] = [];
  const order = ["member", "sheet", "table", "page", "slide", "section", "rows", "row", "lines", "line", "items", "item", "column", "kind"];
  for (const key of order) {
    const value = location[key];
    if (value === undefined || value === null || value === "") continue;
    if (key === "kind") continue;
    if (Array.isArray(value)) {
      if (value.length === 2 && value.every((v) => typeof v === "number")) parts.push(`${key} ${value[0]}–${value[1]}`);
      else if (value.length) parts.push(`${key} ${value.slice(0, 8).join(", ")}${value.length > 8 ? "…" : ""}`);
      continue;
    }
    parts.push(`${key} ${String(value)}`);
  }
  return parts.join(" · ") || "whole file";
}

/** Where a chart's numbers came from, in words. */
export function chartSource(source: Record<string, unknown>): string {
  const base = typeof source.base === "string" ? source.base : "";
  const rows = source.rows;
  if (Array.isArray(rows) && rows.length === 2 && rows.every((r) => typeof r === "number") && !String(base).startsWith("sample")) {
    return `${String(source.table ?? "")} rows ${rows[0]}–${rows[1]} (${base})`.trim();
  }
  if (Array.isArray(rows)) {
    const shown = rows.slice(0, 12).join(", ");
    return `${String(source.table ?? "")} ${base}: rows ${shown}${rows.length > 12 ? ` and ${rows.length - 12} more` : ""}`.trim();
  }
  if (Array.isArray(source.locations)) {
    const locs = (source.locations as unknown[]).flat().slice(0, 6).map((l) => where(l as Location));
    return `${base}: ${locs.join("; ")}`;
  }
  return base;
}

/** The parts still to send, given what the server has. */
export function missingParts(total: number, received: number[]): number[] {
  const have = new Set(received);
  return Array.from({ length: total }, (_, i) => i).filter((i) => !have.has(i));
}

/** The key under which an unfinished upload of this exact file is remembered in this browser. */
export function resumeKey(file: { name: string; size: number; lastModified: number }): string {
  return `rf-upload:${file.name}:${file.size}:${file.lastModified}`;
}

export function money(usd: number): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: usd < 1 ? 4 : 2 }).format(usd);
}
