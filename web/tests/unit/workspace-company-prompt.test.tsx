// @vitest-environment jsdom
// A workspace zip uploaded to an account whose domain is not the one the package names: the
// uploads card shows the question instead of the account picker; "Yes" calls same-company, "No"
// the ordinary confirm-company call with the package's company.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { DocumentStatus } from "@/lib/gen/api.gen";
import { WorkspaceCompanyPrompt, workspaceOf } from "@/components/ws/WorkspaceCompanyPrompt";

const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: () => undefined }) }));

afterEach(() => { cleanup(); apiMock.mockReset(); });

const held: DocumentStatus = {
  document_id: "d1", account_id: "a1", name: "Octavia_workspace.zip", status: "needs_account", stage: "needs_account",
  declared_type: "application/zip", detected_type: "application/zip", size_bytes: 10, reason: null,
  message: "Pick the account this file belongs to.", passages: 3, redactions: 0, redaction_tier: null,
  authoritative: false, needs_company: true, created_at: "2026-11-05T00:00:00Z", parts_total: 1, parts_done: 1,
  parts_failed: [], indexed_passages: 0, members_read: 3, members_skipped: [], audience: "client",
  company: {
    status: "resolved", reason: "workspace_other_company",
    company: { name: "Octavia Bio", domain: "octavia-bio.example", score: 10 },
    workspace: { domain: "octavia-bio.example", name: "Octavia Bio", decision: "mismatch", account_domain: "octavia-us.example", account_name: "Octavia US" },
  },
};

test("a held workspace with another domain is a prompt, not a picker", () => {
  expect(workspaceOf(held)?.domain).toBe("octavia-bio.example");
  expect(workspaceOf({ company: null })).toBeNull();
  expect(workspaceOf({ company: { workspace: { domain: "x.example", decision: "same" } } })).toBeNull();
});

test("yes binds the package's domain to the account", async () => {
  apiMock.mockResolvedValue({});
  render(<WorkspaceCompanyPrompt doc={held} ws={workspaceOf(held)!} />);
  const prompt = screen.getByTestId("workspace-company-prompt");
  expect(prompt.textContent).toContain("Octavia Bio (octavia-bio.example)");
  expect(prompt.textContent).toContain("Octavia US (octavia-us.example)");
  fireEvent.click(screen.getByRole("button", { name: /Yes, same company/ }));
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("POST", "/v1/documents/d1/same-company"));
});

test("no moves the file to the company the package names", async () => {
  apiMock.mockResolvedValue({ account_id: "a2", domain: "octavia-bio.example", document_ids: ["d1"] });
  const done = vi.fn();
  render(<WorkspaceCompanyPrompt doc={held} ws={workspaceOf(held)!} onDone={done} />);
  fireEvent.click(screen.getByRole("button", { name: /No, it belongs to/ }));
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("POST", "/v1/documents/confirm-company", { document_ids: ["d1"], company_name: "Octavia Bio", domain: "octavia-bio.example" }));
  await waitFor(() => expect(done).toHaveBeenCalled());
});
