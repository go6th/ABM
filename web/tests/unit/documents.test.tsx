// @vitest-environment jsdom
// "From your files" research in the browser: file checks before upload, the detected company
// (never guessed when documents disagree), readiness and labels; the drop zone uploads through
// the API, follows each file to Indexed and shows a refused file's reason without uploading it.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { allIndexed, cleanDomain, detectedCompany, refuseReason } from "@/lib/documents";
import { DocumentDrop } from "@/components/ws/DocumentDrop";
import type { DocumentStatus } from "@/lib/gen/api.gen";

const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));
afterEach(() => { cleanup(); apiMock.mockReset(); });

function doc(over: Partial<DocumentStatus>): DocumentStatus {
  return {
    document_id: "d1", account_id: null, name: "a.pdf", status: "parsed", stage: "parsed", declared_type: "pdf",
    detected_type: "pdf", size_bytes: 10, reason: null, message: null, passages: 3, redactions: 0,
    redaction_tier: "standard", authoritative: false, company: null, needs_company: true, created_at: "2026-10-13T00:00:00Z",
    parts_total: 1, parts_done: 0, parts_failed: [], indexed_passages: 0, members_read: 0, members_skipped: [],
    ...over,
  };
}

test("files are checked before any byte is sent", () => {
  expect(refuseReason("call.mp3", 10)).toMatch(/transcript/);
  expect(refuseReason("tool.exe", 10)).toMatch(/not supported/);
  // any size: the server checks the admin's per-file limit
  expect(refuseReason("big.pdf", 30 * 1024 ** 3)).toBeNull();
  expect(refuseReason("empty.txt", 0)).toMatch(/empty/);
  expect(refuseReason("notes.DOCX", 10)).toBeNull();
});

test("the detected company is resolved only when the documents agree", () => {
  const acme = { status: "resolved", company: { name: "Acme", domain: "acme.example" }, candidates: [{ name: "Acme", domain: "acme.example" }] };
  const globex = { status: "resolved", company: { name: "Globex", domain: "globex.example" }, candidates: [{ name: "Globex", domain: "globex.example" }] };
  expect(detectedCompany([doc({ company: acme }), doc({ company: acme })]).company?.domain).toBe("acme.example");
  const split = detectedCompany([doc({ company: acme }), doc({ company: globex })]);
  expect(split.status).toBe("ambiguous");
  expect(split.company).toBeNull();
  expect(split.candidates.map((c) => c.domain)).toEqual(["acme.example", "globex.example"]);
  expect(detectedCompany([doc({ company: null })]).status).toBe("none");
});

test("readiness, domains and labels", () => {
  expect(allIndexed([])).toBe(false);
  expect(allIndexed([doc({ stage: "indexed" }), doc({ stage: "parsed" })])).toBe(false);
  expect(allIndexed([doc({ stage: "indexed" })])).toBe(true);
  expect(cleanDomain(" https://www.Acme.com/about ")).toBe("acme.com");
});

test("a dropped file is uploaded, completed and followed to Ready; a refused one never uploads", async () => {
  let polls = 0;
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "POST" && path === "/v1/documents/uploads") {
      return { document_id: "d1", upload_url: "/v1/documents/d1/content?token=t", method: "PUT", headers: {}, expires_at: "x", max_bytes: 5, direct: false };
    }
    if (method === "PUT") return { document_id: "d1", received_bytes: 5 };
    if (path.endsWith("/complete")) return doc({ stage: "scanning", status: "quarantined" });
    if (path.endsWith("/status")) {
      polls += 1;
      return polls < 2 ? doc({ stage: "scanning", status: "scanning" }) : doc({ stage: "indexed", status: "indexed", needs_company: false });
    }
    throw new Error(`unexpected ${method} ${path}`);
  });
  const seen: DocumentStatus[][] = [];
  render(<DocumentDrop accountId="acc-1" onChange={(d) => seen.push(d)} />);
  const input = screen.getByLabelText("Add files");
  fireEvent.change(input, { target: { files: [new File(["hello"], "notes.pdf"), new File(["x"], "tool.exe")] } });
  await waitFor(() => expect(screen.getByText("Ready")).toBeTruthy(), { timeout: 5000 });
  expect(screen.getByText(/not supported/)).toBeTruthy();
  const started = apiMock.mock.calls.filter((c) => c[1] === "/v1/documents/uploads");
  expect(started).toHaveLength(1);
  expect(started[0]![2]).toMatchObject({ name: "notes.pdf", size_bytes: 5, account_id: "acc-1" });
  expect(apiMock.mock.calls.some((c) => c[0] === "PUT" && c[1] === "/v1/documents/d1/content?token=t")).toBe(true);
  expect(seen.at(-1)?.map((d) => d.stage)).toEqual(["indexed"]);
});

test("Google Drive: files are listed by the server and an added one is fetched into the same path", async () => {
  apiMock.mockImplementation(async (method: string, path: string, body?: unknown) => {
    if (method === "GET" && path.startsWith("/v1/documents/drive-files")) {
      return { files: [{ id: "drive-file-0001", name: "Plan.docx", type: "docx", size_bytes: null, modified_at: "2026-10-14T00:00:00Z", google_native: true }] };
    }
    if (method === "POST" && path === "/v1/documents/from-drive") {
      expect(body).toEqual({ file_id: "drive-file-0001", account_id: null });
      return doc({ document_id: "d9", name: "Plan.docx", stage: "scanning", status: "quarantined", size_bytes: 42 });
    }
    return doc({ document_id: "d9", name: "Plan.docx", stage: "indexed" });
  });
  render(<DocumentDrop onChange={() => undefined} initialDrive />);
  expect(await screen.findByText("Plan.docx")).toBeTruthy();
  expect(screen.getByText(/Google file, added as DOCX/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("POST", "/v1/documents/from-drive", { file_id: "drive-file-0001", account_id: null }));
  expect(await screen.findByRole("button", { name: "Added" })).toBeTruthy();
});

test("Google Drive not connected: a link to the Mail and drive page, no file list", async () => {
  const { ApiError } = await import("@/lib/api");
  apiMock.mockRejectedValue(new ApiError(409, "drive_not_connected", "Connect Google Drive on the Mail and drive page first."));
  render(<DocumentDrop onChange={() => undefined} initialDrive />);
  const link = await screen.findByRole("link", { name: "Connect Google Drive" });
  expect(link.getAttribute("href")).toBe("/mail-and-drive");
  expect(screen.queryByRole("search")).toBeNull();
});

test("after the company is confirmed the files are read again, so they reach Ready", async () => {
  let indexed = false;
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "GET" && path.startsWith("/v1/documents/drive-files")) {
      return { files: [{ id: "drive-file-0002", name: "Deck.pdf", type: "pdf", size_bytes: 9, modified_at: null, google_native: false }] };
    }
    if (method === "POST" && path === "/v1/documents/from-drive") return doc({ document_id: "d7", name: "Deck.pdf", stage: "parsed" });
    if (path === "/v1/documents/d7/status") return doc({ document_id: "d7", name: "Deck.pdf", stage: indexed ? "indexed" : "parsed", needs_company: !indexed });
    return doc({});
  });
  const seen: DocumentStatus[][] = [];
  const { rerender } = render(<DocumentDrop onChange={(d) => seen.push(d)} initialDrive refresh={0} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add" }));
  expect(await screen.findByText("Read, waiting for the company")).toBeTruthy();
  indexed = true; // the company was confirmed: the server indexes the file
  rerender(<DocumentDrop onChange={(d) => seen.push(d)} initialDrive refresh={1} />);
  expect(await screen.findByText("Ready")).toBeTruthy();
  expect(seen.at(-1)?.[0]?.stage).toBe("indexed");
});

test("a file bound to its company but still at parsed keeps being followed to Ready", async () => {
  const { stageOf } = await import("@/lib/documents");
  expect(stageOf({ stage: "parsed", needs_company: true })).toBe("parsed");
  expect(stageOf({ stage: "parsed", needs_company: false })).toBe("indexing");
  let polls = 0;
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "GET" && path.startsWith("/v1/documents/drive-files")) {
      return { files: [{ id: "drive-file-0003", name: "Tech.xlsx", type: "xlsx", size_bytes: 9, modified_at: null, google_native: false }] };
    }
    if (method === "POST" && path === "/v1/documents/from-drive") return doc({ document_id: "d8", name: "Tech.xlsx", stage: "parsed", needs_company: false });
    if (path === "/v1/documents/d8/status") { polls += 1; return doc({ document_id: "d8", name: "Tech.xlsx", stage: "indexed", needs_company: false }); }
    return doc({});
  });
  render(<DocumentDrop onChange={() => undefined} initialDrive />);
  fireEvent.click(await screen.findByRole("button", { name: "Add" }));
  expect(await screen.findByText("Ready", {}, { timeout: 5000 })).toBeTruthy();
  expect(polls).toBeGreaterThan(0);
});

test("a picked folder sends the supported files, ignores hidden ones and lists the rest", async () => {
  const { folderFiles } = await import("@/lib/documents");
  const file = (path: string, size = 10) => Object.assign(new File(["x".repeat(size)], path.split("/").pop()!), { webkitRelativePath: path });
  const { send, skipped } = folderFiles([
    file("Workday/plan.docx"),
    file("Workday/data/contacts.xlsx"),
    file("Workday/pack.zip"),
    file("Workday/.DS_Store"),
    file("Workday/__MACOSX/._plan.docx"),
    file("Workday/logo.png"),
    file("Workday/empty.txt", 0),
  ]);
  expect(send.map((f) => f.name)).toEqual(["plan.docx", "contacts.xlsx", "pack.zip"]);
  expect(skipped).toEqual(["Workday/logo.png", "Workday/empty.txt"]);
});

test("the drop zone offers a folder button and accepts zip files", () => {
  render(<DocumentDrop onChange={() => undefined} />);
  expect(screen.getByRole("button", { name: "Add a folder" })).toBeTruthy();
  const files = screen.getByLabelText("Add files") as HTMLInputElement;
  expect(files.accept.split(",")).toContain(".zip");
  const folder = screen.getByLabelText("Add a folder") as HTMLInputElement;
  expect(folder.hasAttribute("webkitdirectory")).toBe(true);
});
