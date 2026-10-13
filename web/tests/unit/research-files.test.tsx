// @vitest-environment jsdom
// Research files in the browser: wording helpers, chart sources, and the resumable uploader
// (slices the file, sends only the parts the server lacks, retries a part, opens the file page).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { chartSource, fileSize, missingParts, reasonText, resumeKey, where } from "@/lib/research-files";
import { FileUploader } from "@/components/ws/FileUploader";

const push = vi.fn();
vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ push, refresh: vi.fn() }) }));
const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));

beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); apiMock.mockReset(); push.mockReset(); });

test("sizes, reasons and locations read as words", () => {
  expect(fileSize(512)).toBe("512 B");
  expect(fileSize(5 * 1024 * 1024)).toBe("5.0 MB");
  expect(fileSize(3 * 1024 ** 3)).toBe("3.0 GB");
  expect(reasonText("worker_lost")).toMatch(/resume to continue/);
  expect(reasonText("something_new")).toMatch(/Everything read so far is kept/);
  expect(where({ sheet: "Accounts", rows: [2, 5001], column: "email", kind: "email" })).toBe("sheet Accounts · rows 2–5001 · column email");
  expect(where({})).toBe("whole file");
});

test("every chart source names its rows or pages", () => {
  expect(chartSource({ table: "data.csv", rows: [2, 301], base: "every row" })).toBe("data.csv rows 2–301 (every row)");
  const sample = chartSource({ table: "t", rows: Array.from({ length: 20 }, (_, i) => i + 2), base: "sample of 20 of 300 numeric rows" });
  expect(sample).toMatch(/sample of 20 of 300 numeric rows: rows 2, 3/);
  expect(sample).toMatch(/and 8 more/);
  expect(chartSource({ locations: [{ page: 1 }, { page: 3 }], base: "every word" })).toBe("every word: page 1; page 3");
});

test("the upload plan sends only the missing parts", () => {
  expect(missingParts(5, [0, 2, 4])).toEqual([1, 3]);
  expect(resumeKey({ name: "a.csv", size: 10, lastModified: 7 })).toBe("rf-upload:a.csv:10:7");
});

function pick(file: File) {
  fireEvent.change(screen.getByLabelText(/Choose a file/i), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: /Upload/ }));
}

test("a remembered upload resumes with the missing parts only, then opens the file", async () => {
  const file = new File(["a".repeat(25)], "big.csv", { type: "text/csv", lastModified: 1 });
  localStorage.setItem(resumeKey(file), "up-1");
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "GET") return { upload_id: "up-1", status: "open", parts_total: 3, part_size: 10, received: [0, 2], missing: [1], file_id: null };
    if (method === "PUT") return { part: 1, size: 10, sha256: "x" };
    if (path.endsWith("/complete")) return { file_id: "f-1", status: "UPLOADED", deduplicated: false };
    throw new Error(`unexpected ${method} ${path}`);
  });
  render(<FileUploader />);
  pick(file);
  await waitFor(() => expect(push).toHaveBeenCalledWith("/research-files/f-1"));
  const puts = apiMock.mock.calls.filter((c) => c[0] === "PUT");
  expect(puts.map((c) => c[1])).toEqual(["/v1/files/uploads/up-1/parts/1"]);
  expect((puts[0]![2] as Blob).size).toBe(10);
  expect(localStorage.getItem(resumeKey(file))).toBeNull();
});

test("a new upload slices the file, retries a failed part, and remembers itself until done", async () => {
  const file = new File(["b".repeat(15)], "new.csv", { lastModified: 2 });
  let failures = 1;
  apiMock.mockImplementation(async (method: string, path: string, body: unknown) => {
    if (method === "POST" && path === "/v1/files/uploads") {
      expect(body).toEqual({ name: "new.csv", size: 15, mime: null });
      return { upload_id: "up-2", part_size: 10, parts_total: 2 };
    }
    if (method === "PUT") {
      expect(localStorage.getItem(resumeKey(file))).toBe("up-2");
      if (path.endsWith("/1") && failures-- > 0) throw new Error("network");
      return { part: 0, size: 10, sha256: "x" };
    }
    if (path.endsWith("/complete")) return { file_id: "f-2", status: "UPLOADED", deduplicated: false };
    throw new Error(`unexpected ${method} ${path}`);
  });
  render(<FileUploader />);
  pick(file);
  await waitFor(() => expect(push).toHaveBeenCalledWith("/research-files/f-2"), { timeout: 4000 });
  const puts = apiMock.mock.calls.filter((c) => c[0] === "PUT").map((c) => [c[1], (c[2] as Blob).size]);
  expect(puts).toEqual([["/v1/files/uploads/up-2/parts/0", 10], ["/v1/files/uploads/up-2/parts/1", 5], ["/v1/files/uploads/up-2/parts/1", 5]]);
});

test("an upload that keeps failing stops with a resumable message", async () => {
  const file = new File(["c".repeat(5)], "x.csv", { lastModified: 3 });
  const { ApiError } = await import("@/lib/api");
  apiMock.mockImplementation(async (method: string) => {
    if (method === "POST") return { upload_id: "up-3", part_size: 10, parts_total: 1 };
    throw new ApiError(422, "part_size_mismatch", "bad");
  });
  render(<FileUploader />);
  pick(file);
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "The upload stopped. Choose the same file again to continue where it stopped.");
  expect(localStorage.getItem(resumeKey(file))).toBe("up-3"); // kept for the resume
  expect(push).not.toHaveBeenCalled();
});
