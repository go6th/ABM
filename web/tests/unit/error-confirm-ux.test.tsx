// @vitest-environment jsdom
// Client web audit 2026-11-07, error messages and confirmations: a delete asks in a pop-up (never
// window.confirm), says in plain words when it failed, and announces when it worked; a failed
// save shows a sentence, never an HTTP status text or a blank alert; a tab with a file still
// going up warns before it is closed; Team and Support access tabs are for administrators.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ChatThreadSummary, DocumentStatus, ScheduleRow } from "@/lib/gen/api.gen";

const apiMock = vi.fn();
const refresh = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh, push: vi.fn(), replace: vi.fn() }), usePathname: () => "/settings" }));
const serverGet = vi.fn();
vi.mock("@/lib/server-api", async (original) => ({
  ...(await original<typeof import("@/lib/server-api")>()),
  serverGet: (...args: unknown[]) => serverGet(...args),
}));
vi.mock("@/components/Limits", () => ({ LimitNote: () => null, useMyLimits: () => ({ limits: null }) }));

import { ApiError } from "@/lib/api";
import { ChatList } from "@/components/ask/ChatList";
import { DeleteFileButton } from "@/components/accounts/DeleteFileButton";
import { DocumentDrop } from "@/components/ws/DocumentDrop";
import { AutoRefreshSelect } from "@/components/accounts/AutoRefreshSelect";
import { AddAccount } from "@/components/accounts/AddAccount";
import SettingsLayout from "@/app/(client)/settings/layout";

const notes: string[] = [];
const onNote = (e: Event) => notes.push((e as CustomEvent<{ text: string }>).detail.text);
beforeEach(() => { window.addEventListener("ui:announce", onNote); });
afterEach(() => {
  cleanup();
  apiMock.mockReset();
  serverGet.mockReset();
  refresh.mockReset();
  notes.length = 0;
  window.removeEventListener("ui:announce", onNote);
  vi.restoreAllMocks();
});

const THREAD = { id: "t1", title: "Pricing", turn_count: 2, updated_at: "2026-11-06T00:00:00Z" } as ChatThreadSummary;
const STATUS_TEXT = new ApiError(500, "http_500", "Internal Server Error");

function doc(over: Partial<DocumentStatus>): DocumentStatus {
  return {
    document_id: "d1", account_id: null, name: "a.pdf", status: "indexed", stage: "indexed", declared_type: "pdf",
    detected_type: "pdf", size_bytes: 10, reason: null, message: null, passages: 3, redactions: 0,
    redaction_tier: "standard", authoritative: false, company: null, needs_company: false, created_at: "2026-10-13T00:00:00Z",
    parts_total: 1, parts_done: 0, parts_failed: [], indexed_passages: 0, members_read: 0, members_skipped: [], audience: "client",
    ...over,
  };
}

test("deleting a chat asks in a pop-up, then deletes and says so", async () => {
  const confirm = vi.spyOn(window, "confirm");
  apiMock.mockResolvedValue({});
  render(<ChatList threads={[THREAD]} />);
  fireEvent.click(screen.getByRole("button", { name: "Delete Pricing" }));
  expect(screen.getByRole("dialog").textContent).toContain("Its questions and answers are removed");
  expect(apiMock).not.toHaveBeenCalled();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("DELETE", "/v1/chat/threads/t1"));
  await waitFor(() => expect(notes).toEqual(["Chat deleted"]));
  expect(refresh).toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled();
});

test("a chat that could not be deleted keeps the pop-up open with a plain sentence", async () => {
  apiMock.mockRejectedValue(STATUS_TEXT);
  render(<ChatList threads={[THREAD]} />);
  fireEvent.click(screen.getByRole("button", { name: "Delete Pricing" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toBe("The chat could not be deleted. Try again in a moment.");
  expect(notes).toEqual([]);
});

test("deleting a file asks first; Cancel deletes nothing", async () => {
  apiMock.mockResolvedValue({});
  render(<DeleteFileButton documentId="d9" name="plan.docx" />);
  fireEvent.click(screen.getByRole("button", { name: "Delete plan.docx" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(apiMock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Delete plan.docx" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("DELETE", "/v1/documents/d9"));
  await waitFor(() => expect(notes).toEqual(["File removed"]));
});

test("removing a saved upload asks, shows a failed delete, and announces a removal", async () => {
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "POST" && path === "/v1/documents/uploads") return { document_id: "d1", upload_url: "/v1/documents/d1/content?token=t", method: "PUT", headers: {}, expires_at: "x", max_bytes: 5, direct: false };
    if (method === "PUT") return { document_id: "d1", received_bytes: 5 };
    if (path.endsWith("/complete") || path.endsWith("/status")) return doc({});
    throw new Error(`unexpected ${method} ${path}`);
  });
  render(<DocumentDrop accountId="acc-1" onChange={() => undefined} />);
  fireEvent.change(screen.getByLabelText("Add files"), { target: { files: [new File(["hello"], "notes.pdf")] } });
  await screen.findByText("Ready");
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  expect(apiMock.mock.calls.some((c) => c[0] === "DELETE")).toBe(false); // nothing deleted before the answer
  apiMock.mockRejectedValueOnce(new ApiError(503, "http_503", "Service Unavailable"));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove" }));
  expect((await screen.findByRole("alert")).textContent).toBe("It could not be removed. Try again in a moment.");
  expect(document.querySelector(".file-row")).not.toBeNull(); // still listed
  apiMock.mockResolvedValueOnce({});
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(document.querySelector(".file-row")).toBeNull());
  expect(notes).toEqual(["File removed"]);
});

test("while a file is going up the tab warns before it is closed, and stops warning after", async () => {
  let finish: (v: unknown) => void = () => undefined;
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "POST" && path === "/v1/documents/uploads") return { document_id: "d1", upload_url: "/v1/documents/d1/content?token=t", method: "PUT", headers: {}, expires_at: "x", max_bytes: 5, direct: false };
    if (method === "PUT") return new Promise((resolve) => { finish = resolve; });
    return doc({});
  });
  render(<DocumentDrop accountId="acc-1" onChange={() => undefined} />);
  const quiet = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(quiet);
  expect(quiet.defaultPrevented).toBe(false); // nothing in flight yet
  fireEvent.change(screen.getByLabelText("Add files"), { target: { files: [new File(["hello"], "notes.pdf")] } });
  await waitFor(() => expect(apiMock.mock.calls.some((c) => c[0] === "PUT")).toBe(true));
  const warned = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(warned);
  expect(warned.defaultPrevented).toBe(true);
  await act(async () => { finish({ document_id: "d1", received_bytes: 5 }); });
  await screen.findByText("Ready");
  const after = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(after);
  expect(after.defaultPrevented).toBe(false);
});

const ROW = { account_id: "a1", name: "Acme", domain: "acme.example", cadence: "weekly", options: [{ cadence: "weekly", label: "Weekly", allowed: true }, { cadence: "monthly", label: "Monthly", allowed: true }] } as unknown as ScheduleRow;

test("a saved auto refresh is announced; a failed one shows a plain sentence, not the status text", async () => {
  apiMock.mockResolvedValueOnce({ rows: [{ ...ROW, cadence: "monthly" }] });
  render(<AutoRefreshSelect row={ROW} />);
  fireEvent.change(screen.getByLabelText("Auto refresh for Acme"), { target: { value: "monthly" } });
  await waitFor(() => expect(notes).toEqual(["Auto refresh saved"]));
  apiMock.mockRejectedValueOnce(new ApiError(422, "http_422", "Unprocessable Content"));
  fireEvent.change(screen.getByLabelText("Auto refresh for Acme"), { target: { value: "weekly" } });
  expect((await screen.findByRole("alert")).textContent).toBe("Not saved. Try again.");
});

test("adding ready-made accounts: an empty conflict message reads as a sentence, and a missing list offers Try again", async () => {
  const library = { accounts: [{ id: "L1", name: "Globex", in_workspace: false }] } as never;
  apiMock.mockRejectedValue(new ApiError(409, "conflict", ""));
  render(<AddAccount estimate={null} library={library} />);
  fireEvent.click(screen.getByRole("button", { name: "Globex" }));
  fireEvent.click(screen.getByRole("button", { name: "Add 1 to workspace" }));
  expect((await screen.findByRole("alert")).textContent).toBe("The accounts could not be added. Try again in a moment.");
  cleanup();
  render(<AddAccount estimate={null} library={null} />);
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(refresh).toHaveBeenCalled();
});

async function tabs(role: string | null) {
  serverGet.mockResolvedValue(role ? { role } : null);
  render(await SettingsLayout({ children: <p>body</p> }));
  return screen.getAllByRole("link").map((a) => a.textContent);
}

test("only administrators see the Team and Support access tabs", async () => {
  expect(await tabs("admin")).toEqual(["Models", "Auto refresh", "Team", "Support access"]);
  cleanup();
  expect(await tabs("analyst")).toEqual(["Models", "Auto refresh"]);
});
