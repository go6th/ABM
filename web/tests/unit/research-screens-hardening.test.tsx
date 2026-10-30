// @vitest-environment jsdom
// The web research screens hold up in production: the table's downloads name the account from an
// explicit domain (encoded once), older answers without tabs, rows, filters or blocks still
// render, a retried start after a network error sends the same request id (the server answers it
// with the job already started, so nothing is paid for twice) while a refusal gets a new one, a
// fast double click posts once, a download part only links to the server's own part address, and
// the tabs and the board are usable from the keyboard and a screen reader.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

const api = vi.fn();
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace }),
  usePathname: () => "/research",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public readonly status: number, public readonly type: string, public readonly detail: string) {
      super(`${status} ${type}`);
    }
  }
  return { api: (...args: unknown[]) => api(...args), ApiError, newIdempotencyKey: () => "k" };
});
vi.mock("@/components/Limits", () => ({
  LimitNote: () => null,
  LimitReached: () => null,
  limitLine: () => "",
  limitReached: () => null,
  useMyLimits: () => ({ limits: null, reload: vi.fn() }),
}));
vi.mock("@/components/ModelMenu", () => ({
  ResearchModelChips: () => null,
  useResearchModels: () => ({ body: {} }),
}));
vi.mock("@/components/ws/DocumentDrop", () => ({ DocumentDrop: () => null }));

import { exportHref, SheetView } from "@/components/accounts/SheetView";
import { SheetTabs, tabHref } from "@/components/accounts/SheetTabs";
import { TabView } from "@/components/accounts/TabView";
import { ResumeButton } from "@/components/accounts/ResumeResearch";
import { ResearchCompany } from "@/components/accounts/ResearchCompany";
import { DownloadEverything, partHref } from "@/components/accounts/DownloadEverything";
import { FilesCard } from "@/components/accounts/FilesCard";
import { RefreshForm } from "@/components/accounts/RefreshForm";
import { NewResearch } from "@/components/accounts/NewResearch";
import { ResearchBoard } from "@/components/research/ResearchBoard";
import { ApiError } from "@/lib/api";
import type { RefreshOptions, ResearchEstimate, ResearchLive, Sheet, SheetTab } from "@/lib/gen/api.gen";

afterEach(() => {
  cleanup();
  api.mockReset();
  replace.mockReset();
});

const sheet: Sheet = {
  key: "people",
  title: "People",
  file: "People list.xlsx",
  scope: "",
  footnote: "",
  private: false,
  account_name: "Synthetic Ltd",
  columns: [{ key: "name", label: "Name", sortable: true, kind: "strong" }],
  filters: [{ key: "all", label: "All", count: 1 }],
  filter: "all",
  sort: "name",
  dir: "asc",
  total: 1,
  offset: 0,
  rows: [{ n: 1, cells: { name: "Synthetic Director" }, link: null }],
};

// --- the table -----------------------------------------------------------------------------

test("the CSV and Excel links name the account from the domain prop, encoded once", () => {
  render(<SheetView sheet={{ ...sheet, key: "mail and drive" }} domain="a b.example" base="/accounts/a%20b.example/mail%20and%20drive" moreHref={null} />);
  expect(screen.getByRole("link", { name: "Download table as Excel" }).getAttribute("href"))
    .toBe("/v1/accounts/a%20b.example/sheets/mail%20and%20drive/xlsx?filter=all&sort=name&dir=asc");
  expect(screen.getByRole("link", { name: "Download CSV" }).getAttribute("href"))
    .toBe("/v1/accounts/a%20b.example/sheets/mail%20and%20drive/csv?filter=all&sort=name&dir=asc");
  expect(exportHref("x.example", sheet, "csv")).toBe("/v1/accounts/x.example/sheets/people/csv?filter=all&sort=name&dir=asc");
});

test("a table without an All chip still offers its downloads, and Expand is a plain button", () => {
  render(<SheetView sheet={{ ...sheet, filters: [] }} domain="x.example" base="/accounts/x.example/people" moreHref={null} />);
  expect(screen.getByRole("link", { name: "Download table as Excel" })).toBeTruthy();
  const expand = screen.getByRole("button", { name: "Expand" });
  expect(expand.getAttribute("aria-pressed")).toBeNull();
  fireEvent.click(expand);
  expect(screen.getByRole("button", { name: "Shrink" })).toBeTruthy();
  expect(screen.getByRole("region", { name: "People table" }).className).toContain("big");
});

test("an answer missing rows, filters and cells renders the empty state instead of crashing", () => {
  const old = { ...sheet, rows: undefined, filters: undefined, total: 0 } as unknown as Sheet;
  render(<SheetView sheet={old} domain="x.example" base="/accounts/x.example/people" moreHref={null} />);
  expect(screen.getByText("Nothing here yet")).toBeTruthy();
  cleanup();
  const holes = { ...sheet, rows: [{ n: 1, cells: {}, link: null }] } as Sheet;
  render(<SheetView sheet={holes} domain="x.example" base="/accounts/x.example/people" moreHref={null} />);
  expect(screen.getAllByRole("row")).toHaveLength(2);
});

test("an unsafe link in a cell is shown as text, never as a link", () => {
  const bad = {
    ...sheet,
    columns: [{ key: "profile", label: "LinkedIn", sortable: false, kind: "url" }],
    rows: [{ n: 1, cells: { profile: "Open profile" }, links: { profile: "javascript:alert(1)" }, link: null }],
  } as Sheet;
  render(<SheetView sheet={bad} domain="x.example" base="/accounts/x.example/people" moreHref={null} />);
  expect(screen.queryByRole("link", { name: "Open profile" })).toBeNull();
  expect(screen.getByText("Open profile")).toBeTruthy();
});

// --- the sheet tabs and one sheet of the file ------------------------------------------------

test("the tabs survive an answer without them and mark the open one", () => {
  const { container } = render(<SheetTabs tabs={undefined} base="/accounts/x.example/jobs" current={null} />);
  expect(container.innerHTML).toBe("");
  cleanup();
  const tabs = [{ key: "summary", label: "Summary" }, { key: "jobs", label: "Jobs", main: true }, { key: "long text", label: "Long text" }];
  render(<SheetTabs tabs={tabs} base="/accounts/x.example/jobs" current="long text" />);
  const nav = screen.getByRole("navigation", { name: "Sheets of the file" });
  expect(within(nav).getByRole("link", { name: "Long text" }).getAttribute("aria-current")).toBe("page");
  expect(within(nav).getByRole("link", { name: "Jobs" }).getAttribute("href")).toBe("/accounts/x.example/jobs");
  expect(tabHref("/b", { key: "long text", label: "L" })).toBe("/b?tab=long+text");
});

test("a sheet of the file: missing blocks, rows wider than the header, captions and unsafe links", () => {
  const tab = {
    account_name: "X", file: "Jobs.xlsx", key: "jobs", tab: "counts", tab_label: "Counts", title: "Job postings", tabs: [],
    blocks: undefined,
  } as unknown as SheetTab;
  render(<TabView tab={tab} />);
  expect(screen.getByText("This sheet is empty")).toBeTruthy();
  cleanup();
  render(<TabView tab={{
    ...tab,
    blocks: [
      { kind: "text", text: "Counts by team" },
      { kind: "table", header: ["Team"], rows: [[{ text: "Data" }, { text: "4" }], [{ text: "Bad", href: "javascript:alert(1)" }]] },
    ],
  }} />);
  const table = screen.getByRole("table", { name: /Counts table 1, 2 rows/ });
  expect(within(table).getAllByRole("columnheader")).toHaveLength(3);
  expect(within(table).getByText("4")).toBeTruthy();
  expect(within(table).queryByRole("link")).toBeNull();
});

// --- starting, resuming and downloading ------------------------------------------------------

test("resume: a network error keeps the request id, a refusal gets a new one, a double click posts once", async () => {
  let release: (v: unknown) => void = () => undefined;
  api.mockImplementationOnce(() => Promise.reject(new TypeError("network")));
  render(<ResumeButton runId="r1" />);
  fireEvent.click(screen.getByRole("button", { name: "Resume research" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/could not be resumed/));
  api.mockImplementationOnce(() => Promise.reject(new ApiError(409, "job_open", "")));
  fireEvent.click(screen.getByRole("button", { name: "Resume research" }));
  // an empty reason never shows an empty alert
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/could not be resumed/));
  api.mockImplementationOnce(() => new Promise((r) => { release = r; }));
  const button = screen.getByRole("button", { name: "Resume research" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(api).toHaveBeenCalledTimes(3);
  const ids = api.mock.calls.map((c) => (c[2] as { request_id: string }).request_id);
  expect(ids[1]).toBe(ids[0]); // retried after the network error: the same request
  expect(ids[2]).not.toBe(ids[1]); // after a refusal: a new request
  await act(async () => release({ job_id: "j", resumes_run_id: "r1", domain: "x", status: "queued", steps_done: 1, steps_total: 2 }));
});

const estimate = { minutes: 30, usage: { tokens: 1000, cost_usd: 1, runs: 0 } } as unknown as ResearchEstimate;

test("start research: retrying after a network error resends the same request id", async () => {
  api.mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce({ job_id: "j1", status: "queued", credits: 1, domain: "acme.example" });
  const onStarted = vi.fn();
  render(<ResearchCompany estimate={estimate} onStarted={onStarted} />);
  fireEvent.change(screen.getByLabelText("Company name"), { target: { value: "Acme" } });
  fireEvent.change(screen.getByLabelText("Website"), { target: { value: "acme.example" } });
  fireEvent.click(screen.getByRole("button", { name: "Start research" }));
  fireEvent.click(screen.getByRole("button", { name: "Proceed" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/could not be started/));
  fireEvent.click(screen.getByRole("button", { name: "Proceed" }));
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  const [a, b] = api.mock.calls.map((c) => (c[2] as { request_id: string }).request_id);
  expect(b).toBe(a);
});

test("download everything: one click posts once, and a part only links to the server's part address", async () => {
  let release: (v: unknown) => void = () => undefined;
  api.mockImplementationOnce(() => new Promise((r) => { release = r; }));
  render(<DownloadEverything domain="x.example" />);
  const button = screen.getByRole("button", { name: "Download everything" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(api).toHaveBeenCalledTimes(1);
  expect((screen.getByRole("button", { name: "Starting…" }) as HTMLButtonElement).disabled).toBe(true);
  await act(async () => release({
    job_id: "d1", domain: "x.example", status: "ready", failure: null, files: 2, total_bytes: 10,
    links_expire_at: new Date(Date.now() + 3_600_000).toISOString(),
    parts: [{ n: 1, name: "a.zip", bytes: 5, url: "/v1/downloads/d1/parts/1?token=t" }, { n: 2, name: "b.zip", bytes: 5, url: "javascript:alert(1)" }],
  }));
  expect(screen.getByRole("link", { name: "Download part 1" }).getAttribute("href")).toBe("/v1/downloads/d1/parts/1?token=t");
  expect(screen.queryByRole("link", { name: "Download part 2" })).toBeNull();
  expect(partHref("https://evil.example/v1/downloads/d1/parts/1")).toBeNull();
  expect(partHref("/v1/downloads/d1/parts/x")).toBeNull();
});

test("files: a pick of a file no longer listed is dropped, and the ids are encoded", () => {
  const file = (id: string) => ({ id, name: id, meta: "", kind: "xlsx", filename: `${id}.xlsx`, built_at: "2026-10-27" });
  const { rerender } = render(<FilesCard domain="x.example" files={[file("a"), file("b")]} />);
  expect(screen.getByRole("link", { name: "Download selected (2)" })).toBeTruthy();
  rerender(<FilesCard domain="x.example" files={[file("b"), file("c&d")]} />);
  const selected = screen.getByRole("link", { name: "Download selected (1)" });
  expect(selected.getAttribute("href")).toBe("/v1/accounts/x.example/deliverables/download?ids=b");
  fireEvent.click(screen.getByRole("checkbox", { name: /c&d/ }));
  expect(screen.getByRole("link", { name: "Download selected (2)" }).getAttribute("href")).toBe("/v1/accounts/x.example/deliverables/download?ids=b,c%26d");
  cleanup();
  render(<FilesCard domain="x.example" files={undefined} />);
  expect(screen.getByText("Files appear here when research finishes.")).toBeTruthy();
});

test("refresh: a workspace offered no scopes opens on the topic instead of crashing", () => {
  const view = {
    domain: "acme.example", name: "Acme", refreshed_days_ago: null, allowed: true, cap_per_pull: 0,
    credits: { period: "", pool: 0, used: 0, held: 0, left: 0, resets_on: "" }, open_job: null, options: [],
    topic: { minutes: 8, credits: 40, affordable: true, usage: { tokens: 400000, cost_usd: 2, runs: 0 } },
  } as unknown as RefreshOptions;
  render(<RefreshForm view={view} initialTopic="" />);
  expect(screen.getByRole("radio", { name: /A specific topic/ }).getAttribute("aria-checked")).toBe("true");
  expect((screen.getByRole("button", { name: "Confirm research" }) as HTMLButtonElement).disabled).toBe(true);
});

test("new research: the two tabs are one tab stop and arrow keys move between them", () => {
  render(<NewResearch estimate={estimate} initialTab="company" />);
  const company = screen.getByRole("tab", { name: "Company name" });
  const file = screen.getByRole("tab", { name: "Upload a file" });
  expect(company.tabIndex).toBe(0);
  expect(file.tabIndex).toBe(-1);
  expect(screen.getByRole("tabpanel", { name: "Company name" })).toBeTruthy();
  fireEvent.keyDown(company, { key: "ArrowRight" });
  expect(file.getAttribute("aria-selected")).toBe("true");
  expect(document.activeElement).toBe(file);
  expect(screen.getByRole("tabpanel", { name: "Upload a file" })).toBeTruthy();
  fireEvent.keyDown(file, { key: "Home" });
  expect(company.getAttribute("aria-selected")).toBe("true");
});

// --- the Research board ----------------------------------------------------------------------

const item = {
  job_id: "11111111-1111-1111-1111-111111111111", company: "Acme", domain: "acme.example", kind: "New research", scope: "Everything",
  topic: null, label: "Step 2 of 8", open: true, status: "running", run_id: "r1", run_status: "RUNNING", can_stop: true,
  created_at: null, started_at: null, finished_at: null, findings: 0, searches: 0, steps_done: 1, steps_total: 8, step: null, tokens: 0,
};
const live = { as_of: "2026-10-27T00:00:00Z", detail: null, running: [item], recent: [] } as unknown as ResearchLive;

test("the board: Show names its company, only the connection state is announced, Start controls its form", () => {
  render(<ResearchBoard initial={live} estimate={estimate} />);
  const show = screen.getByRole("button", { name: "Show Acme" });
  fireEvent.click(show);
  expect(replace).toHaveBeenCalledWith(`/research?r=${item.job_id}`, { scroll: false });
  const statuses = screen.getAllByRole("status");
  expect(statuses.map((s) => s.textContent)).toContain("Connecting");
  const start = screen.getByRole("button", { name: "Start new research" });
  expect(start.getAttribute("aria-controls")).toBeNull();
  fireEvent.click(start);
  expect(start.getAttribute("aria-controls")).toBe("research-start");
  expect(document.getElementById("research-start")).toBeTruthy();
});

test("the board's load failure reloads the page instead of a client navigation that changes nothing", () => {
  render(<ResearchBoard initial={null} estimate={estimate} />);
  const reload = screen.getByRole("link", { name: "Refresh" });
  expect(reload.getAttribute("href")).toBe("/research");
});
