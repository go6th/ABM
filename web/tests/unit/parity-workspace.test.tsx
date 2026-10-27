// @vitest-environment jsdom
// The delivered-files section: one tab per file, the download buttons (never the internal file
// for the zip), the counts banner from the shared module, and a table page read from the API
// with the file's labels, chips and links.
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, expect, test, vi } from "vitest";
import type { ParityScreen, ParityTablePage } from "@/lib/gen/api.gen";

const apiMock = vi.fn();
vi.mock("@/lib/api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class ApiError extends Error { status: number; detail: string; constructor(status: number, code: string, detail: string) { super(detail); this.status = status; this.detail = detail; } },
}));

const { ParityWorkspace } = await import("@/components/accounts/ParityWorkspace");

afterEach(() => cleanup());

const bundle = JSON.parse(readFileSync(path.resolve(__dirname, "../../../tests/parity/fixtures/synthetic_bundle.json"), "utf8"));

function screenOf(summaryOverride?: Record<string, Record<string, unknown>>): ParityScreen {
  return {
    version: 3,
    status: "built",
    run_id: "11111111-1111-4111-8111-111111111111",
    created_at: "2026-03-02T10:00:00+00:00",
    meta: bundle.meta,
    computed: bundle.computed,
    ranking: bundle.ranking,
    summary: summaryOverride ?? { "Acme_People_List.xlsx": { "people.senior": bundle.computed.senior } },
    tables: [
      { id: "summary_legend", file: "summary", sheet: null, title: "How to read this file", paginated: false, columns: [{ key: "grade", header: "Grade", kind: "grade", link_label: null, fills: null }, { key: "meaning", header: "Meaning", kind: "long", link_label: null, fills: null }, { key: "use", header: "Use in outreach", kind: "text", link_label: null, fills: null }], total: 4 },
      { id: "people", file: "people", sheet: "People", title: "People", paginated: true, columns: [{ key: "n", header: "No.", kind: "num", link_label: null, fills: null }, { key: "name", header: "Name", kind: "text", link_label: null, fills: null }, { key: "priority", header: "Priority", kind: "priority", link_label: null, fills: { A: "DDEFE0", B: "FFF1CC", C: "EEEEEE" } }, { key: "profile", header: "LinkedIn", kind: "link", link_label: "Open profile", fills: null }], total: 7 },
    ],
    prose: bundle.prose,
    count_rows: { jobs_rows: bundle.jobs_rows, people_rows: bundle.people_rows },
    files: [
      { name: "Acme_Account_Summary.docx", file: "summary", size: 20480, sha256: "a".repeat(64), mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
      { name: "Acme_People_List.xlsx", file: "people", size: 10240, sha256: "b".repeat(64), mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
      { name: "Acme_workspace.zip", file: "workspace", size: 30000, sha256: "c".repeat(64), mime: "application/zip" },
    ],
    download_all: "/v1/accounts/acme.example/parity/files/Acme_workspace.zip?version=3",
  };
}

const peoplePage: ParityTablePage = {
  id: "people", file: "people", title: "People", total: 7, offset: 0, limit: 100,
  columns: screenOf().tables[1]!.columns,
  rows: [[{ text: "1" }, { text: "Pat Example" }, { text: "A" }, { text: "Open profile", href: "https://profiles.example/in/pat-example" }]],
};

test("tabs per file, downloads, the counts banner and a table page", async () => {
  apiMock.mockImplementation(async (_m: string, p: string) => {
    if (p.includes("/parity/tables/people")) return peoplePage;
    return { id: "summary_legend", file: "summary", title: "How to read this file", total: 4, offset: 0, limit: 100, columns: screenOf().tables[0]!.columns, rows: [[{ text: "Confirmed" }, { text: "Read in a primary source during this run" }, { text: "May be stated" }]] };
  });
  const { container } = render(<ParityWorkspace domain="acme.example" screen={screenOf()} />);
  const tabs = screen.getByRole("navigation", { name: "Delivered files" });
  expect(within(tabs).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Account Summary", "People List"]);
  const all = screen.getByTestId("download-all");
  expect(all.getAttribute("href")).toBe("/v1/accounts/acme.example/parity/files/Acme_workspace.zip?version=3");
  expect(screen.getByTestId("download-summary").getAttribute("href")).toBe("/v1/accounts/acme.example/parity/files/Acme_Account_Summary.docx?version=3");
  await waitFor(() => expect(screen.getByText("Confirmed")).toBeTruthy());

  within(tabs).getByRole("tab", { name: "People List" }).click();
  await waitFor(() => expect(screen.getByTestId("download-people")).toBeTruthy());
  expect(screen.getByTestId("counts-ok")).toBeTruthy();
  await waitFor(() => expect(screen.getByText("Pat Example")).toBeTruthy());
  const table = container.querySelector('[data-table="people"] table.xl')!;
  expect(Array.from(table.querySelectorAll("thead th span.th")).map((h) => h.textContent)).toEqual(["No.", "Name", "Priority", "LinkedIn"]);
  const chip = table.querySelector(".pchip")!;
  expect(chip.textContent).toBe("A");
  expect(chip.className).toContain("fill-DDEFE0");
  const link = table.querySelector("a.src-link")!;
  expect(link.textContent).toBe("Open profile");
  expect(link.getAttribute("href")).toBe("https://profiles.example/in/pat-example");
  expect(container.innerHTML).not.toContain("style=");
});

test("a count that differs from the manifest shows the error banner", async () => {
  apiMock.mockResolvedValue(peoplePage);
  render(<ParityWorkspace domain="acme.example" screen={screenOf({ "Acme_People_List.xlsx": { "people.senior": 42 } })} />);
  screen.getByRole("tab", { name: "People List" }).click();
  await waitFor(() => expect(screen.getByTestId("counts-mismatch")).toBeTruthy());
  expect(screen.getByRole("alert").textContent).toContain("people.senior");
});
