// @vitest-environment jsdom
// "View what was read": a file's passages load when opened, with where each came from, and
// "Load more" fetches the next page.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { WhatWasRead } from "@/components/accounts/WhatWasRead";

const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));
afterEach(() => { cleanup(); apiMock.mockReset(); });

test("passages load on open and page with Load more", async () => {
  apiMock
    .mockResolvedValueOnce({ document_id: "d1", next_after: 49, passages: [{ seq: 0, kind: "section", where: "page 1", heading: "Plan", content: "Renewal in March." }] })
    .mockResolvedValueOnce({ document_id: "d1", next_after: null, passages: [{ seq: 50, kind: "section", where: "page 9", heading: null, content: "Budget owner is Dana." }] });
  render(<WhatWasRead documentId="d1" name="plan.docx" />);
  expect(apiMock).not.toHaveBeenCalled(); // nothing loads until opened
  fireEvent.click(screen.getByRole("button", { name: "View what was read" }));
  expect(await screen.findByText("Renewal in March.")).toBeTruthy();
  expect(screen.getByText(/page 1 · Plan/)).toBeTruthy();
  expect(apiMock).toHaveBeenCalledWith("GET", "/v1/documents/d1/passages?after=-1&limit=50");
  fireEvent.click(screen.getByRole("button", { name: "Load more" }));
  expect(await screen.findByText("Budget owner is Dana.")).toBeTruthy();
  expect(apiMock).toHaveBeenLastCalledWith("GET", "/v1/documents/d1/passages?after=49&limit=50");
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
});
