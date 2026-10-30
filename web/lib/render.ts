// Safe rendering of untrusted text and links (VW4). Every cell is rendered as a React text node
// (escaped by React); links survive only as absolute http(s) URLs without credentials and open
// with rel="noopener noreferrer". Nothing here builds HTML.

export function safeLink(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw || raw.length > 2048 || /[\s\u0000-\u001f\\]/.test(raw) || raw.startsWith("//")) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (!url.hostname || url.username || url.password) return null;
  return raw;
}

const CONTROL = /[\u0000-\u0008\u000b-\u001f\u007f‪-‮⁦-⁩‎‏]/g;

export function cellText(value: unknown, cap = 2000): string {
  if (value === null || value === undefined) return "";
  const text = String(value).replace(CONTROL, "").replace(/\s+/g, " ").trim();
  return text.length <= cap ? text : `${text.slice(0, cap - 1)}…`;
}

export const LINK_REL = "noopener noreferrer";

const ARCHIVE_PAGE = /^(?:\/v1)?(\/accounts\/[^/]+\/archive)$/;

/** The website's own archive page for an archived-copy address, whatever origin a delivered file
 * carries (the public origin of the day, or the older API path `/v1/accounts/{domain}/archive`):
 * a same-origin relative address, so the session cookie is sent and the link never leaves the
 * site. Anything else is returned unchanged (null stays null). */
export function ownHref(href: string | null): string | null {
  if (!href) return href;
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return href;
  }
  const page = ARCHIVE_PAGE.exec(url.pathname);
  const original = url.searchParams.get("url");
  if (!page || !original) return href;
  return `${page[1]}?${new URLSearchParams({ url: original }).toString()}`;
}
