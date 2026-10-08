// The client-safe drop (U3, F4): before a tenant-plane reply is validated or rendered, every field
// whose NAME is internal (amounts, credits, provider codes) and every string VALUE that matches the
// client-output denylist (config/client_output_denylist.txt, generated) is removed, and each removal
// is reported by path (never by value). The API already withholds these; this is the second wall.
import { DENYLIST_PATTERNS } from "@/lib/gen/labels.gen";

export const DENIED_KEYS: readonly RegExp[] = [
  /(^|_)micros$/i, /(^|_)usd$/i, /^cost(_|$)/i, /(^|_)cost$/i, /credit/i, /^provider(_code)?s?$/i,
  /^by_provider$/i, /^ledger/i, /^call_ref/i, /^args_hash$/i, /^secret/i, /token$/i,
];
// The CSRF token is the one *_token field a tenant reply may carry (it never renders).
const ALLOWED_KEYS = new Set(["csrf_token"]);
const VALUE_PATTERNS = DENYLIST_PATTERNS.map((p) => new RegExp(p, "i"));
const PROVIDER_CODE = /\bp_[a-z]+\b/;

export interface Dropped { path: string; reason: "key" | "value" }

export function deniedKey(key: string): boolean {
  return !ALLOWED_KEYS.has(key) && DENIED_KEYS.some((re) => re.test(key));
}

export function deniedValue(value: string): boolean {
  return PROVIDER_CODE.test(value) || VALUE_PATTERNS.some((re) => re.test(value));
}

export function dropDenied(input: unknown, path = "$", dropped: Dropped[] = []): { value: unknown; dropped: Dropped[] } {
  if (typeof input === "string") {
    if (deniedValue(input)) { dropped.push({ path, reason: "value" }); return { value: undefined, dropped }; }
    return { value: input, dropped };
  }
  if (Array.isArray(input)) {
    const out: unknown[] = [];
    input.forEach((item, i) => {
      const next = dropDenied(item, `${path}[${i}]`, dropped).value;
      if (next !== undefined) out.push(next);
    });
    return { value: out, dropped };
  }
  if (input !== null && typeof input === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(input as Record<string, unknown>)) {
      if (deniedKey(key)) { dropped.push({ path: `${path}.${key}`, reason: "key" }); continue; }
      const next = dropDenied(item, `${path}.${key}`, dropped).value;
      if (next !== undefined) out[key] = next;
      else if (item === null) out[key] = null;
    }
    return { value: out, dropped };
  }
  return { value: input, dropped };
}
