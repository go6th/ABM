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
    parts_total: 1, parts_done: 0, parts_failed: [], indexed_passages: 0, members_read: 0, members_skipped: [], audience: "client",
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

test("after the company is confirmed the files are read again, so they reach Ready", async () => {
  let indexed = false;
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "POST" && path === "/v1/documents/uploads") {
      return { document_id: "d7", upload_url: "/v1/documents/d7/content?token=t", method: "PUT", headers: {}, expires_at: "x", max_bytes: 5, direct: false };
    }
    if (method === "PUT") return { document_id: "d7", received_bytes: 5 };
    if (path.endsWith("/complete")) return doc({ document_id: "d7", name: "Deck.pdf", stage: "parsed" });
    if (path === "/v1/documents/d7/status") return doc({ document_id: "d7", name: "Deck.pdf", stage: indexed ? "indexed" : "parsed", needs_company: !indexed });
    return doc({});
  });
  const seen: DocumentStatus[][] = [];
  const { rerender } = render(<DocumentDrop onChange={(d) => seen.push(d)} refresh={0} />);
  fireEvent.change(screen.getByLabelText("Add files"), { target: { files: [new File(["hello"], "Deck.pdf")] } });
  expect(await screen.findByText("Read, waiting for the company")).toBeTruthy();
  indexed = true; // the company was confirmed: the server indexes the file
  rerender(<DocumentDrop onChange={(d) => seen.push(d)} refresh={1} />);
  expect(await screen.findByText("Ready")).toBeTruthy();
  expect(seen.at(-1)?.[0]?.stage).toBe("indexed");
});

test("a file bound to its company but still at parsed keeps being followed to Ready", async () => {
  const { stageOf } = await import("@/lib/documents");
  expect(stageOf({ stage: "parsed", needs_company: true })).toBe("parsed");
  expect(stageOf({ stage: "parsed", needs_company: false })).toBe("indexing");
  let polls = 0;
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "POST" && path === "/v1/documents/uploads") {
      return { document_id: "d8", upload_url: "/v1/documents/d8/content?token=t", method: "PUT", headers: {}, expires_at: "x", max_bytes: 5, direct: false };
    }
    if (method === "PUT") return { document_id: "d8", received_bytes: 5 };
    if (path.endsWith("/complete")) return doc({ document_id: "d8", name: "Tech.xlsx", stage: "parsed", needs_company: false });
    if (path === "/v1/documents/d8/status" && polls === 0) { polls += 1; return doc({ document_id: "d8", name: "Tech.xlsx", stage: "parsed", needs_company: false }); }
    if (path === "/v1/documents/d8/status") { polls += 1; return doc({ document_id: "d8", name: "Tech.xlsx", stage: "indexed", needs_company: false }); }
    return doc({});
  });
  render(<DocumentDrop onChange={() => undefined} />);
  fireEvent.change(screen.getByLabelText("Add files"), { target: { files: [new File(["hello"], "Tech.xlsx")] } });
  expect(await screen.findByText("Ready", {}, { timeout: 5000 })).toBeTruthy();
  expect(polls).toBeGreaterThan(1);
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

test("Owner only ticked: the upload starts with audience owner_only and the row says so", async () => {
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "POST" && path === "/v1/documents/uploads") {
      return { document_id: "d5", upload_url: "/v1/documents/d5/content?token=t", method: "PUT", headers: {}, expires_at: "x", max_bytes: 5, direct: false };
    }
    if (method === "PUT") return { document_id: "d5", received_bytes: 5 };
    if (path.endsWith("/complete")) return doc({ document_id: "d5", stage: "scanning", status: "quarantined", audience: "owner_only" });
    if (path.endsWith("/status")) return doc({ document_id: "d5", stage: "indexed", status: "indexed", needs_company: false, audience: "owner_only" });
    throw new Error(`unexpected ${method} ${path}`);
  });
  render(<DocumentDrop accountId="acc-1" onChange={() => undefined} />);
  const box = screen.getByRole("checkbox", { name: /Owner only: visible to you and admins/ }) as HTMLInputElement;
  expect(box.checked).toBe(false);
  fireEvent.click(box);
  expect(box.checked).toBe(true);
  fireEvent.change(screen.getByLabelText("Add files"), { target: { files: [new File(["hello"], "private.pdf")] } });
  await waitFor(() => expect(screen.getByText("Ready")).toBeTruthy(), { timeout: 5000 });
  const started = apiMock.mock.calls.filter((c) => c[1] === "/v1/documents/uploads");
  expect(started).toHaveLength(1);
  expect(started[0]![2]).toMatchObject({ name: "private.pdf", account_id: "acc-1", audience: "owner_only" });
  expect(screen.getByText(/Owner only$/)).toBeTruthy();
});

test("Owner only unticked: the start request carries no audience field", async () => {
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "POST" && path === "/v1/documents/uploads") {
      return { document_id: "d6", upload_url: "/v1/documents/d6/content?token=t", method: "PUT", headers: {}, expires_at: "x", max_bytes: 5, direct: false };
    }
    if (method === "PUT") return { document_id: "d6", received_bytes: 5 };
    if (path.endsWith("/complete")) return doc({ document_id: "d6", stage: "scanning", status: "quarantined" });
    if (path.endsWith("/status")) return doc({ document_id: "d6", stage: "indexed", status: "indexed", needs_company: false });
    throw new Error(`unexpected ${method} ${path}`);
  });
  render(<DocumentDrop accountId="acc-1" onChange={() => undefined} />);
  fireEvent.change(screen.getByLabelText("Add files"), { target: { files: [new File(["hello"], "plain.pdf")] } });
  await waitFor(() => expect(screen.getByText("Ready")).toBeTruthy(), { timeout: 5000 });
  const started = apiMock.mock.calls.filter((c) => c[1] === "/v1/documents/uploads");
  expect(Object.keys(started[0]![2] as object)).not.toContain("audience");
});
