// @vitest-environment jsdom
// The deep-dive page opened from an Ask citation (?match_col=location&match=…, owner 2026-11-04):
// it forwards the match to the API, shows the removable chip the API echoes, and keeps the match
// on its paging links and tabs. A refused match reads as an unavailable view, never a crash.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Sheet } from "@/lib/gen/api.gen";

const serverGet = vi.fn();
vi.mock("@/lib/server-api", async (original) => ({
  ...(await original<typeof import("@/lib/server-api")>()),
  serverGet: (...args: unknown[]) => serverGet(...args),
}));

import SheetPage from "@/app/(client)/accounts/[domain]/[sheet]/page";

afterEach(cleanup);

const SHEET: Sheet = {
  key: "jobs", title: "Job postings", file: "Job postings.xlsx", scope: "Open roles.", footnote: "Note.",
  private: false, account_name: "Synthetic Holdings", from_file: false, sheet_name: "Sheet 1",
  columns: [{ key: "title", label: "Role", sortable: true, kind: "strong" }, { key: "location", label: "Location", sortable: true, kind: "text" }],
  filters: [{ key: "all", label: "All", count: 450 }],
  filter: "all", sort: "posted", dir: "desc", total: 450, offset: 0,
  match: { col: "location", label: "Location", value: "San Francisco" },
  rows: Array.from({ length: 200 }, (_, i) => ({ n: i + 1, cells: { title: `Role ${i + 1}`, location: "San Francisco, US" }, links: {}, link: null })),
  tabs: [{ key: "summary", label: "Summary", main: false }, { key: "jobs", label: "Jobs", main: true }],
  file_id: null,
};

function open(search: Record<string, string>) {
  return SheetPage({ params: Promise.resolve({ domain: "holdings.example", sheet: "jobs" }), searchParams: Promise.resolve(search) });
}

test("the page forwards the match, shows its chip and keeps it on the paging links and tabs", async () => {
  serverGet.mockClear();
  serverGet.mockResolvedValue(SHEET);
  render(await open({ match_col: "location", match: "San Francisco" }));
  expect(serverGet.mock.calls[0]![0]).toBe("/v1/accounts/holdings.example/sheets/jobs?limit=200&match_col=location&match=San+Francisco");
  const chip = screen.getByRole("link", { name: "Remove the match Location: San Francisco" });
  expect(chip.getAttribute("href")).toBe("/accounts/holdings.example/jobs?filter=all&sort=posted&dir=desc");
  expect(screen.getByRole("link", { name: "Show more rows" }).getAttribute("href")).toContain("match_col=location&match=San+Francisco");
  expect(screen.getByRole("link", { name: "Summary" }).getAttribute("href")).toBe("/accounts/holdings.example/jobs?tab=summary&match_col=location&match=San+Francisco");
  expect(screen.getByRole("link", { name: "Jobs" }).getAttribute("href")).toBe("/accounts/holdings.example/jobs?match_col=location&match=San+Francisco");
});

test("a page without a match sends none and shows no chip", async () => {
  serverGet.mockClear();
  serverGet.mockResolvedValue({ ...SHEET, match: null });
  render(await open({}));
  expect(serverGet.mock.calls[0]![0]).toBe("/v1/accounts/holdings.example/sheets/jobs?limit=200");
  expect(screen.queryByRole("link", { name: /Remove the match/ })).toBeNull();
  expect(screen.getByRole("link", { name: "Jobs" }).getAttribute("href")).toBe("/accounts/holdings.example/jobs");
});

test("a match the API refuses reads as a view that is not available", async () => {
  serverGet.mockClear();
  serverGet.mockResolvedValue(null); // a 422 the page asked serverGet to pass through as null
  const { container } = render(await open({ match_col: "grade", match: "x" }));
  expect(container.textContent).toContain("That filter or sort is not available");
});
