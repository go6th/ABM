// @vitest-environment jsdom
// Files go straight to storage in resumable chunks (owner decision 2026-10-14): a dropped chunk
// is asked about and continued from the committed offset, nothing is sent twice, the page can
// reload and the same file resumes from the session kept in this browser.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { committedFrom, pausedUploads, uploadDocument } from "@/lib/upload";

const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));

/** A storage session: commits whole chunks, answers 308 with the committed range. */
class Storage {
  bytes = 0;
  puts: string[] = [];
  dropNext = false;
  afterCommit: (() => void) | null = null;
  constructor(public total: number) {}
}
let storage: Storage;

class FakeXhr {
  status = 0;
  upload: { onprogress: ((e: { loaded: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  private headers: Record<string, string> = {};
  private range: string | null = null;
  open(): void {}
  setRequestHeader(name: string, value: string): void { this.headers[name] = value; }
  getResponseHeader(name: string): string | null { return name === "Range" ? this.range : null; }
  abort(): void { this.onabort?.(); }
  send(body: Blob | null): void {
    const header = this.headers["Content-Range"] ?? "";
    storage.puts.push(header);
    queueMicrotask(() => {
      const chunk = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(header);
      if (chunk && storage.dropNext) {
        storage.dropNext = false;
        storage.bytes = Number(chunk[2]) + 1; // the bytes arrived, the answer was lost
        this.onerror?.();
        return;
      }
      if (chunk && body) {
        if (Number(chunk[1]) <= storage.bytes) storage.bytes = Math.max(storage.bytes, Number(chunk[2]) + 1);
        this.upload.onprogress?.({ loaded: body.size });
      }
      this.status = storage.bytes >= storage.total ? 200 : 308;
      this.range = storage.bytes ? `bytes=0-${storage.bytes - 1}` : null;
      const after = chunk ? storage.afterCommit : null;
      if (after) { storage.afterCommit = null; after(); return; } // the page went away first
      this.onload?.();
    });
  }
}

beforeEach(() => {
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  localStorage.clear();
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); apiMock.mockReset(); });

function started(chunk: number) {
  return { document_id: "doc-1", upload_url: "http://store.test/upload/s1", method: "PUT", headers: {}, expires_at: new Date(Date.now() + 3600_000).toISOString(), max_bytes: 0, direct: true, protocol: "resumable", chunk_bytes: chunk };
}

test("the committed range is read from a 308", () => {
  expect(committedFrom("bytes=0-1023")).toBe(1024);
  expect(committedFrom(null)).toBe(0);
});

test("a dropped chunk continues from the committed offset and nothing is sent twice", async () => {
  const file = new File([new Uint8Array(10_000)], "big.csv", { lastModified: 1 });
  storage = new Storage(file.size);
  apiMock.mockImplementation(async (method: string, path: string) => (path === "/v1/documents/uploads" ? started(4096) : { document_id: "doc-1" }));
  const progress: number[] = [];
  storage.dropNext = false;
  const run = uploadDocument(file, null, (sent) => progress.push(sent), new AbortController().signal);
  // drop the answer to the second chunk
  await vi.waitFor(() => expect(storage.puts.length).toBeGreaterThanOrEqual(2));
  storage.dropNext = true;
  await vi.runAllTimersAsync();
  await expect(run).resolves.toBe("doc-1");
  const chunks = storage.puts.filter((p) => !p.includes("*"));
  expect(new Set(chunks).size).toBe(chunks.length); // no chunk sent twice
  expect(storage.bytes).toBe(file.size);
  expect(progress.at(-1)).toBe(file.size);
  expect(apiMock).toHaveBeenCalledWith("POST", "/v1/documents/doc-1/complete");
  expect(pausedUploads()).toEqual([]);
});

test("after a reload the same file resumes from the session kept in this browser", async () => {
  const file = new File([new Uint8Array(9_000)], "notes.txt", { lastModified: 7 });
  storage = new Storage(file.size);
  apiMock.mockImplementation(async (method: string, path: string) => (path === "/v1/documents/uploads" ? started(3000) : { document_id: "doc-1" }));
  const controller = new AbortController();
  storage.afterCommit = () => controller.abort(); // the page goes away mid-file
  const first = uploadDocument(file, null, () => undefined, controller.signal);
  await expect(first).rejects.toThrow();
  expect(storage.bytes).toBe(3000);
  expect(pausedUploads().map((p) => p.documentId)).toEqual(["doc-1"]);
  const before = storage.bytes;
  apiMock.mockClear();
  await expect(uploadDocument(file, null, () => undefined, new AbortController().signal)).resolves.toBe("doc-1");
  expect(apiMock).not.toHaveBeenCalledWith("POST", "/v1/documents/uploads", expect.anything());
  expect(storage.puts.find((p) => p.startsWith(`bytes ${before}-`))).toBeTruthy();
  expect(storage.bytes).toBe(file.size);
});
