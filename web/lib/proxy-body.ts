// Size limits for what the /v1 proxy forwards. The proxy runs before the API has authenticated
// the caller, so it never buffers a body: it checks the declared length, and counts the bytes
// as they stream through so a body with no (or a false) length is cut off at the limit too.

/** Every JSON call: a question, a setting, an invite. */
export const JSON_BODY_LIMIT = 1024 * 1024;
/** The local stand-in for a signed upload URL (dev object store). The API enforces the admin's
 * per-file limit; this is only the outer bound, and the body is streamed, never held. */
export const UPLOAD_BODY_LIMIT = 21 * 1024 ** 3;

const UPLOAD_PATH = /^\/v1\/documents\/[^/]+\/content$/;

export function bodyLimit(method: string, pathname: string): number {
  return method === "PUT" && UPLOAD_PATH.test(pathname) ? UPLOAD_BODY_LIMIT : JSON_BODY_LIMIT;
}

/** A JSON call answers within this; past it the proxy answers 504 instead of hanging. */
export const JSON_TIMEOUT_MS = 120_000;
/** A file going up or down (uploads, downloads, exports, workbooks) may take far longer. */
export const TRANSFER_TIMEOUT_MS = 60 * 60_000;

// a table's CSV streams every row (thousands of long descriptions): a transfer, not a JSON call
const TRANSFER_PATH = /^\/v1\/(downloads\/|exports\/[^/]+\/files\/|audit\/export$|accounts\/[^/]+\/sheets\/[^/]+\/(xlsx|csv)$)/;

export function proxyTimeout(method: string, pathname: string): number {
  if (bodyLimit(method, pathname) === UPLOAD_BODY_LIMIT) return TRANSFER_TIMEOUT_MS;
  return method === "GET" && TRANSFER_PATH.test(pathname) ? TRANSFER_TIMEOUT_MS : JSON_TIMEOUT_MS;
}

/** The declared length when it is a number; null when absent. NaN counts as over any limit. */
export function declaredLength(header: string | null): number | null {
  if (header === null || header.trim() === "") return null;
  const n = Number(header);
  return Number.isInteger(n) && n >= 0 ? n : Number.POSITIVE_INFINITY;
}

export interface CappedBody { stream: ReadableStream<Uint8Array>; exceeded: () => boolean }

/** Pass the body through, erroring once more than `limit` bytes have gone by. */
export function capBody(source: ReadableStream<Uint8Array>, limit: number): CappedBody {
  let seen = 0;
  let over = false;
  const counter = new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      seen += chunk.byteLength;
      if (seen > limit) {
        over = true;
        controller.error(new Error("body_too_large"));
        return;
      }
      controller.enqueue(chunk);
    },
  });
  return { stream: source.pipeThrough(counter), exceeded: () => over };
}
