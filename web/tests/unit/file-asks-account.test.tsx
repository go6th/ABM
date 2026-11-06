// @vitest-environment jsdom
// "Upload a file" (owner, 2026-11-05): a file is never attached to an account by itself. Once it is
// read the page asks which account it is for and its company link, prefilled from what the file
// names, and nothing is sent until Continue is pressed.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { NewResearch } from "@/components/accounts/NewResearch";
import type { DocumentStatus } from "@/lib/gen/api.gen";

const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  api: (...args: unknown[]) => apiMock(...args),
  newIdempotencyKey: () => "key-1",
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("@/components/Limits", () => ({
  LimitNote: () => null,
  LimitReached: () => null,
  limitReached: () => null,
  useMyLimits: () => ({ limits: null }),
}));

const read: DocumentStatus = {
  document_id: "doc-1", account_id: null, name: "UCB_workspace.xlsx", status: "parsed", stage: "parsed", declared_type: "xlsx", detected_type: "xlsx",
  size_bytes: 10, reason: null, message: null, passages: 3, redactions: 0, redaction_tier: "standard", authoritative: false, audience: "client",
  company: { status: "resolved", company: { name: "UCB", domain: "www.UCB.com/about" }, candidates: [] },
  needs_company: true, created_at: "2026-11-05T00:00:00Z", parts_total: 1, parts_done: 1, parts_failed: [], indexed_passages: 0, members_read: 0, members_skipped: [],
} as unknown as DocumentStatus;

// the drop zone reports the file once, as the real one does when the read is done
vi.mock("@/components/ws/DocumentDrop", () => ({
  DocumentDrop: ({ onChange }: { onChange: (docs: DocumentStatus[]) => void }) => {
    useEffect(() => { onChange([read]); }, [onChange]);
    return null;
  },
}));

afterEach(() => apiMock.mockReset());

test("a read file asks for the account and its link and sends nothing until Continue", async () => {
  apiMock.mockResolvedValue({ account_id: "acc-1", domain: "ucb.com", document_ids: ["doc-1"] });
  render(<NewResearch estimate={null} initialTab="file" />);

  expect(await screen.findByText("Which company is this file for?")).toBeTruthy();
  expect((screen.getByLabelText("Company name") as HTMLInputElement).value).toBe("UCB");
  // the detected link is shown in the shape the server accepts, not as the file wrote it
  expect((screen.getByLabelText("Website") as HTMLInputElement).value).toBe("ucb.com");
  expect(apiMock).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await waitFor(() => expect(apiMock).toHaveBeenCalledTimes(1));
  expect(apiMock).toHaveBeenCalledWith("POST", "/v1/documents/confirm-company", { document_ids: ["doc-1"], company_name: "UCB", domain: "ucb.com" });
  expect(await screen.findByText("UCB")).toBeTruthy();
});

test("Continue stays off until the link is a company link", async () => {
  render(<NewResearch estimate={null} initialTab="file" />);
  const link = await screen.findByLabelText("Website");
  fireEvent.change(link, { target: { value: "not a link" } });
  expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(link, { target: { value: "https://www.Example.org/page" } });
  expect((screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement).disabled).toBe(false);
});
