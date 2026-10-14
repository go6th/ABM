// The one place the API's address is decided (server side only). In production API_ORIGIN must
// be set (the image and the Cloud Run service set it); there is no fallback to a compose service
// name, so a missing value fails closed instead of reaching an unintended host. Outside
// production, a local API on the developer's machine is the default.
export const DEV_API_ORIGIN = "http://127.0.0.1:8080";

/** The API origin without a trailing slash, or null when production has none configured. */
export function apiOrigin(): string | null {
  const configured = process.env.API_ORIGIN?.trim();
  if (configured) return configured.replace(/\/$/, "");
  return process.env.NODE_ENV === "production" ? null : DEV_API_ORIGIN;
}
