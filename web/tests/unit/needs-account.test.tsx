// @vitest-environment jsdom
// An upload the server holds as "Needs account" (ingestion hardening PR5): the file card says so,
// names the company the file looks like it is about, and lets the user pick an account, which
// calls confirm-company. The Ask answer shows the "still being indexed" notice as a muted line.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AccountPicker } from "@/components/ws/AccountPicker";
import { UploadedFilesCard } from "@/components/accounts/UploadedFilesCard";
import { AnswerView } from "@/components/ask/AnswerView";
import { STAGE_TEXT, detectedLine, isSettled, stageOf, stageTone } from "@/lib/documents";
import type { ChatAnswer, DocumentStatus } from "@/lib/gen/api.gen";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));

beforeEach(() => { apiMock.mockReset(); refresh.mockReset(); });
afterEach(cleanup);

const held = (over: Partial<DocumentStatus> = {}): DocumentStatus => ({
  document_id: "d1", account_id: null, name: "Notes.docx", status: "needs_account", stage: "needs_account", declared_type: "docx",
  detected_type: "docx", size_bytes: 1024, reason: null, message: "Pick the account this file belongs to.", passages: 12, redactions: 0, redaction_tier: "standard",
  authoritative: false, needs_company: true, created_at: "2026-11-04T00:00:00Z", parts_total: 1, parts_done: 1, parts_failed: [], indexed_passages: 0, members_read: 0, members_skipped: [], audience: "client",
  company: { status: "resolved", company: { name: "Northwind Traders Inc", domain: "northwind.example", score: 2.5 }, candidates: [] },
  ...over,
} as DocumentStatus);

const accounts = { accounts: [
  { id: "a1", domain: "northwind.example", name: "Northwind", latest_run: null },
  { id: "a2", domain: "acme.example", name: null, latest_run: null },
], next_after: null };

test("the stage reads Needs account, warns, and is not polled as working", () => {
  expect(STAGE_TEXT.needs_account).toBe("Needs account");
  expect(stageTone("needs_account")).toBe("warn");
  expect(stageOf(held())).toBe("needs_account");
  expect(isSettled("needs_account")).toBe(true);
  expect(detectedLine(held())).toBe("Northwind Traders Inc (northwind.example)");
  expect(detectedLine(held({ company: { status: "ambiguous", company: null, candidates: [] } }))).toBeNull();
});

test("the picker names the detected company and confirms the chosen account", async () => {
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "GET" && path.startsWith("/v1/accounts")) return accounts;
    return { account_id: "a1", domain: "northwind.example", document_ids: ["d1"] };
  });
  const done = vi.fn();
  render(<AccountPicker doc={held()} onConfirmed={done} />);
  expect(screen.getByText("Pick the account this file belongs to.")).toBeTruthy();
  expect(screen.getByText(/looks like it is about Northwind Traders Inc \(northwind.example\)/)).toBeTruthy();
  const button = screen.getByRole("button", { name: "Use this account" }) as HTMLButtonElement;
  expect(button.disabled).toBe(true); // nothing chosen yet
  await screen.findByRole("option", { name: "Northwind (northwind.example)" });
  fireEvent.change(screen.getByLabelText("Account for Notes.docx"), { target: { value: "a1" } });
  fireEvent.click(button);
  await waitFor(() => expect(done).toHaveBeenCalled());
  expect(apiMock).toHaveBeenCalledWith("POST", "/v1/documents/confirm-company", { document_ids: ["d1"], company_name: "Northwind", domain: "northwind.example" });
});

test("an account without a name is confirmed by its domain, and a refusal is shown", async () => {
  apiMock.mockImplementation(async (method: string) => {
    if (method === "GET") return accounts;
    throw new Error("nope");
  });
  render(<AccountPicker doc={held({ company: null })} />);
  expect(screen.getByText("No company could be matched from the file.")).toBeTruthy();
  await screen.findByRole("option", { name: "acme.example" });
  fireEvent.change(screen.getByLabelText("Account for Notes.docx"), { target: { value: "a2" } });
  fireEvent.click(screen.getByRole("button", { name: "Use this account" }));
  await screen.findByRole("alert");
  expect(apiMock).toHaveBeenLastCalledWith("POST", "/v1/documents/confirm-company", { document_ids: ["d1"], company_name: "acme.example", domain: "acme.example" });
});

test("the account page lists a held upload with its picker, preselected to the bound account", async () => {
  apiMock.mockResolvedValue(accounts);
  render(<UploadedFilesCard docs={[held({ account_id: "a1" })]} domain="northwind.example" />);
  expect(screen.getByText("Needs account")).toBeTruthy();
  await screen.findByRole("option", { name: "Northwind (northwind.example)" });
  expect((screen.getByLabelText("Account for Notes.docx") as HTMLSelectElement).value).toBe("a1");
});

test("the Ask answer shows the indexing notice as a muted line", () => {
  const answer = {
    account_domain: null, account_name: null, answered: true, followups: [], items: null, offer: null,
    paragraphs: [[{ t: "text", v: "Plain text" }]], sources: [], notices: ["Some files are still being indexed; search may miss them."],
    used: { mail_drive: "not_connected", web: "not_used", workspace: "used" },
  } as unknown as ChatAnswer;
  render(<AnswerView answer={answer} openSource={null} onOpenSource={() => undefined} offerDismissed={false} onDismissOffer={() => undefined} onFollowUp={() => undefined} onPickAccount={() => undefined} busy={false} />);
  expect(screen.getByRole("status").textContent).toBe("Some files are still being indexed; search may miss them.");
});
