// The resumable-upload sessions this browser keeps so the same file, chosen again, continues
// where it stopped. An entry holds a session URL that can write into one document's quarantine
// object, so it is kept per signed-in user (the key carries the user id) and every entry is
// removed at sign-out. With no signed-in user known, nothing is read or kept (fail closed).

export interface Saved { documentId: string; name: string; size: number; url: string; protocol: "resumable" | "single"; chunk: number; expires: string; sent: number }

const VERSION = "v2";
const PREFIXES = ["upload:v1:", `upload:${VERSION}:`];
let owner: string | null = null;

/** Set from the signed-in user's id when the workspace loads; null when signed out. */
export function setUploadOwner(userId: string | null): void {
  owner = userId && userId.length > 0 ? userId : null;
}

export function uploadKey(file: Pick<File, "name" | "size" | "lastModified">): string | null {
  return owner ? `upload:${VERSION}:${owner}:${file.name}:${file.size}:${file.lastModified}` : null;
}

function ownPrefix(): string | null {
  return owner ? `upload:${VERSION}:${owner}:` : null;
}

export function readSaved(key: string | null): Saved | null {
  if (!key) return null;
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as Saved) : null;
  } catch {
    return null;
  }
}

export function writeSaved(key: string | null, value: Saved | null): void {
  if (!key) return;
  try {
    if (value) localStorage.setItem(key, JSON.stringify(value));
    else localStorage.removeItem(key);
  } catch {
    // storage unavailable (a private window): the upload works, it just cannot resume
  }
}

/** Uploads this user's browser left unfinished (choose the same file again to continue). */
export function pausedUploads(): Saved[] {
  const prefix = ownPrefix();
  if (!prefix) return [];
  const out: Saved[] = [];
  try {
    const keys: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key?.startsWith(prefix)) keys.push(key);
    }
    for (const key of keys) {
      const saved = readSaved(key);
      if (saved && new Date(saved.expires).getTime() > Date.now()) out.push(saved);
      else localStorage.removeItem(key);
    }
  } catch {
    return [];
  }
  return out;
}

/** Forget an unfinished upload (the user removed it). */
export function forgetPaused(saved: Saved): void {
  const prefix = ownPrefix();
  if (!prefix) return;
  try {
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key?.startsWith(prefix) && readSaved(key)?.documentId === saved.documentId) {
        localStorage.removeItem(key);
        return;
      }
    }
  } catch {
    // nothing kept
  }
}

/** Sign-out: remove every kept session of every user, including the unscoped v1 entries. */
export function clearUploadSessions(): void {
  owner = null;
  try {
    const keys: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key && PREFIXES.some((p) => key.startsWith(p))) keys.push(key);
    }
    for (const key of keys) localStorage.removeItem(key);
  } catch {
    // nothing kept
  }
}
