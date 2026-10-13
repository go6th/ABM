// @vitest-environment jsdom
// "From your files" research in the browser: file checks before upload, the detected company
// (never guessed when documents disagree), readiness and labels; the drop zone uploads through
// the API, follows each file to Indexed and shows a refused file's reason without uploading it.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { allIndexed, cleanDomain, detectedCompany, refuseReason, sourceLabel } from "@/lib/documents";
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
    ...over,
  };
}

test("files are checked before any byte is sent", () => {
  expect(refuseReason("call.mp3", 10, 100)).toMatch(/transcript/);
  expect(refuseReason("tool.exe", 10, 100)).toMatch(/not supported/);
  expect(refuseReason("big.pdf", 101, 100)).toMatch(/limit/);
  expect(refuseReason("empty.txt", 0, 100)).toMatch(/empty/);
  expect(refuseReason("notes.DOCX", 10, 100)).toBeNull();
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
  expect(sourceLabel("file")).toBe("From your files");
  expect(sourceLabel("company")).toBe("Public research");
});

test("a dropped file is uploaded, completed and followed to Indexed; a refused one never uploads", async () => {
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
  const input = screen.getByLabelText(/Choose files to research/i);
  fireEvent.change(input, { target: { files: [new File(["hello"], "notes.pdf"), new File(["x"], "tool.exe")] } });
  await waitFor(() => expect(screen.getByText("Indexed")).toBeTruthy(), { timeout: 5000 });
  expect(screen.getByText(/not supported/)).toBeTruthy();
  const started = apiMock.mock.calls.filter((c) => c[1] === "/v1/documents/uploads");
  expect(started).toHaveLength(1);
  expect(started[0]![2]).toMatchObject({ name: "notes.pdf", size_bytes: 5, account_id: "acc-1" });
  expect(apiMock.mock.calls.some((c) => c[0] === "PUT" && c[1] === "/v1/documents/d1/content?token=t")).toBe(true);
  expect(seen.at(-1)?.map((d) => d.stage)).toEqual(["indexed"]);
});
