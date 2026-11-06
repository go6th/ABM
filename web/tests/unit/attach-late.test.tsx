// @vitest-environment jsdom
// "From a file": files that finish reading after the company was confirmed join that company, so
// none is left at "Read, waiting for the company" once the choice has closed (2026-10-15).
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useAttachLate } from "@/components/accounts/NewResearch";
import { ApiError } from "@/lib/api";
import type { DocumentStatus } from "@/lib/gen/api.gen";

const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
afterEach(() => apiMock.mockReset());

const doc = (id: string, stage: string): DocumentStatus => ({
  document_id: id, account_id: null, name: `${id}.xlsx`, status: stage, stage, declared_type: "xlsx", detected_type: "xlsx",
  size_bytes: 10, reason: null, message: null, passages: 3, redactions: 0, redaction_tier: "standard", authoritative: false, audience: "client",
  company: null, needs_company: true, created_at: "2026-10-15T00:00:00Z", parts_total: 1, parts_done: 1, parts_failed: [], indexed_passages: 0, members_read: 0, members_skipped: [],
});

test("late files are attached to the confirmed company once, still-reading ones when they finish", async () => {
  apiMock.mockResolvedValue({ account_id: "acc-1", domain: "workday.com" });
  const done = vi.fn();
  const company = { name: "Workday Inc", domain: "workday.com" };
  const { rerender } = renderHook(({ waiting }) => useAttachLate(waiting, company, "acc-1", done), {
    initialProps: { waiting: [doc("people", "parsed"), doc("tech", "parsing")] },
  });
  await waitFor(() => expect(apiMock).toHaveBeenCalledTimes(1));
  expect(apiMock).toHaveBeenCalledWith("POST", "/v1/documents/confirm-company", { document_ids: ["people"], company_name: "Workday Inc", domain: "workday.com" });
  rerender({ waiting: [doc("people", "parsed"), doc("tech", "parsed")] });
  await waitFor(() => expect(apiMock).toHaveBeenCalledTimes(2));
  expect(apiMock.mock.calls[1]![2]).toMatchObject({ document_ids: ["tech"] });
  await waitFor(() => expect(done).toHaveBeenCalledTimes(2));
});

test("a refused request is not sent again on every read, a network error is", async () => {
  apiMock.mockRejectedValueOnce(new ApiError(422, "validation", "refused"));
  const company = { name: "Workday Inc", domain: "workday.com" };
  const first = renderHook(({ waiting }) => useAttachLate(waiting, company, "acc-1", vi.fn()), { initialProps: { waiting: [doc("people", "parsed")] } });
  await waitFor(() => expect(apiMock).toHaveBeenCalledTimes(1));
  first.rerender({ waiting: [doc("people", "parsed"), doc("tech", "parsing")] });
  first.rerender({ waiting: [doc("people", "parsed"), doc("tech", "parsing")] });
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(apiMock).toHaveBeenCalledTimes(1);

  apiMock.mockReset();
  apiMock.mockRejectedValueOnce(new TypeError("network")).mockResolvedValue({ account_id: "acc-1", domain: "workday.com" });
  const second = renderHook(({ waiting }) => useAttachLate(waiting, company, "acc-1", vi.fn()), { initialProps: { waiting: [doc("people", "parsed")] } });
  await waitFor(() => expect(apiMock).toHaveBeenCalledTimes(1));
  second.rerender({ waiting: [doc("people", "parsed"), doc("tech", "parsing")] });
  await waitFor(() => expect(apiMock).toHaveBeenCalledTimes(2));
});

test("nothing is attached before a company is confirmed", () => {
  renderHook(() => useAttachLate([doc("people", "parsed")], null, null, vi.fn()));
  expect(apiMock).not.toHaveBeenCalled();
});
