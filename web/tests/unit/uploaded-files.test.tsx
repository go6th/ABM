// @vitest-environment jsdom
// The account page lists the files uploaded for it (they were stored but shown nowhere).
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { UploadedFilesCard } from "@/components/accounts/UploadedFilesCard";
import type { DocumentStatus } from "@/lib/gen/api.gen";

const refreshMock = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: refreshMock }) }));
const uploadMock = vi.fn();
vi.mock("@/lib/upload", async (original) => ({ ...(await original<typeof import("@/lib/upload")>()), uploadDocument: (...args: unknown[]) => uploadMock(...args) }));
const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));

afterEach(cleanup);

const doc = (over: Partial<DocumentStatus>): DocumentStatus => ({
  document_id: "d1", account_id: "a1", name: "Workday_People_List.xlsx", status: "indexed", stage: "indexed", declared_type: "xlsx",
  detected_type: "xlsx", size_bytes: 59392, reason: null, message: null, passages: 420, redactions: 0, redaction_tier: "standard",
  authoritative: false, company: null, needs_company: false, created_at: "2026-10-15T00:00:00Z", parts_total: 1, parts_done: 1, parts_failed: [], indexed_passages: 0, members_read: 0, members_skipped: [], audience: "client",
  ...over,
});

test("each uploaded file shows its name, size, passages and status", () => {
  render(<UploadedFilesCard docs={[doc({}), doc({ document_id: "d2", name: "Tech.xlsx", stage: "parsed", status: "parsed", passages: 0 })]} />);
  expect(screen.getByText("Workday_People_List.xlsx")).toBeTruthy();
  expect(screen.getByText(/420 sections/)).toBeTruthy();
  expect(screen.getByText("Ready")).toBeTruthy();
  expect(screen.getByText("Getting ready")).toBeTruthy(); // bound to the account, on its way to Ready
  expect(screen.getByRole("link", { name: "Add files" }).getAttribute("href")).toBe("/accounts/import");
  // each file can be deleted on its own (it asks first)
  expect(screen.getByRole("button", { name: "Delete Workday_People_List.xlsx" })).toBeTruthy();
});

test("with no uploads the card says where they will appear", () => {
  render(<UploadedFilesCard docs={[]} />);
  expect(screen.getByText("Files you upload for this account appear here.")).toBeTruthy();
});

test("a zip lists the files inside it, each with its role, status and reason, when opened", async () => {
  apiMock.mockResolvedValue({
    document_id: "d3",
    read: 2, skipped: 1, failed: 0,
    members: [
      { member: "ws/deliverables/BD_Job_Postings.xlsx", role: "deliverable_jobs", declared_type: "xlsx", bytes: 3051060, status: "read", reason: null, passages: 1898, tables: 2, audience: "client" },
      { member: "ws/notes/pull_log_phase1.md", role: "note", declared_type: "md", bytes: 20303, status: "read", reason: null, passages: 9, tables: 0, audience: "client" },
      { member: "ws/BD_Owner_Only.docx", role: "owner_only", declared_type: "docx", bytes: 12159, status: "skipped", reason: "an owner-only file; upload it on its own", passages: 0, tables: 0, audience: "owner_only" },
    ],
  });
  render(<UploadedFilesCard docs={[doc({ document_id: "d3", name: "BD_workspace.zip", members_read: 2, members_skipped: [{ member: "ws/BD_Owner_Only.docx", reason: "an owner-only file; upload it on its own" }] })]} />);
  fireEvent.click(screen.getByRole("button", { name: "View the files inside" }));
  expect(await screen.findByText(/3 files: 2 read, 1 skipped, 0 failed/)).toBeTruthy();
  expect(apiMock).toHaveBeenCalledWith("GET", "/v1/documents/d3/members");
  expect(screen.getByText("ws/deliverables/BD_Job_Postings.xlsx")).toBeTruthy();
  expect(screen.getByText("Job postings")).toBeTruthy();
  expect(screen.getByText("Read: 1,898 sections, 2 tables")).toBeTruthy();
  expect(screen.getByText("Owner-only file · owner only")).toBeTruthy();
  expect(screen.getByText("Skipped: an owner-only file; upload it on its own")).toBeTruthy();
});

test("an owner-only upload says so on its line", () => {
  render(<UploadedFilesCard docs={[doc({ audience: "owner_only" })]} />);
  expect(screen.getByText("Owner only: visible to you and admins")).toBeTruthy();
});

test("with the account's domain each file's name opens its page, and the files inside link to their section", async () => {
  apiMock.mockResolvedValue({
    document_id: "d3", read: 1, skipped: 0, failed: 0,
    members: [{ member: "ws/notes/plan.md", role: "note", declared_type: "md", bytes: 20, status: "read", reason: null, passages: 9, tables: 0, audience: "client" }],
  });
  render(<UploadedFilesCard domain="acme.com" docs={[doc({ document_id: "d3", name: "BD_workspace.zip", members_read: 1 })]} />);
  expect(screen.getByRole("link", { name: "BD_workspace.zip" }).getAttribute("href")).toBe("/accounts/acme.com/documents/d3");
  fireEvent.click(screen.getByRole("button", { name: "View the files inside" }));
  expect((await screen.findByRole("link", { name: "ws/notes/plan.md" })).getAttribute("href")).toBe("/accounts/acme.com/documents/d3#m-0");
});

test("on the account's page Add files uploads into that account in a pop-up, not Import a file", async () => {
  uploadMock.mockResolvedValue("d9");
  apiMock.mockResolvedValue(doc({ document_id: "d9", name: "notes.pdf", account_id: "a1", stage: "scanning", status: "scanning", passages: 0 }));
  const { container } = render(
    <div className="ui-pastel"><main><UploadedFilesCard docs={[]} domain="acme.com" account={{ id: "a1", domain: "acme.com", name: "Acme" }} /></main></div>,
  );
  expect(container.querySelector('a[href="/accounts/import"]')).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add files" }));
  const dialog = await screen.findByRole("dialog", { name: "Add files to Acme" });
  expect(dialog.textContent).toContain("go into Acme only");
  const input = within(dialog).getByLabelText("Add files", { selector: "input:not([webkitdirectory])" });
  fireEvent.change(input, { target: { files: [new File(["x"], "notes.pdf", { type: "application/pdf" })] } });
  // the upload carries this account's id, so the server binds the file to it from the start
  await waitFor(() => expect(uploadMock).toHaveBeenCalled());
  expect(uploadMock.mock.calls[0]![1]).toBe("a1");
  expect(await within(dialog).findByText("1 file added. Reading carries on after you close this.")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "Done" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(refreshMock).toHaveBeenCalled();
});
