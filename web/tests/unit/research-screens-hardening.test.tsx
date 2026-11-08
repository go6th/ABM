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
  const errorText = (cause: unknown, fallback: string) => (cause instanceof ApiError && cause.detail?.trim() ? cause.detail : fallback);
  return { api: (...args: unknown[]) => api(...args), ApiError, errorText, newIdempotencyKey: () => "k" };
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

test("the Excel link names the account from the domain prop, encoded once", () => {
  render(<SheetView sheet={{ ...sheet, key: "platform teams" }} domain="a b.example" base="/accounts/a%20b.example/platform%20teams" moreHref={null} />);
  expect(screen.getByRole("link", { name: "Download table as Excel" }).getAttribute("href"))
    .toBe("/v1/accounts/a%20b.example/sheets/platform%20teams/xlsx?filter=all&sort=name&dir=asc");
  expect(screen.queryByRole("link", { name: "Download CSV" })).toBeNull(); // one table export, not two
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
  // Resume research, then "Yes, resume" and "Yes, resume it" (it asks twice)
  const askTwice = () => {
    fireEvent.click(screen.getByRole("button", { name: "Resume research" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, resume" }));
  };
  askTwice();
  fireEvent.click(screen.getByRole("button", { name: "Yes, resume it" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/could not be resumed/));
  api.mockImplementationOnce(() => Promise.reject(new ApiError(409, "job_open", "")));
  askTwice();
  fireEvent.click(screen.getByRole("button", { name: "Yes, resume it" }));
  // an empty reason never shows an empty alert
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/could not be resumed/));
  api.mockImplementationOnce(() => new Promise((r) => { release = r; }));
  askTwice();
  const button = screen.getByRole("button", { name: "Yes, resume it" });
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
  fireEvent.click(screen.getByRole("button", { name: "Start research" })); // Proceed: arms
  fireEvent.click(screen.getByRole("button", { name: /^Yes, start/ }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/could not be started/));
  fireEvent.click(screen.getByRole("button", { name: /^Yes, start/ }));
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

test("files: nothing is ticked until picked, a pick of a file no longer listed is dropped, and the ids are encoded", () => {
  const file = (id: string) => ({ id, name: id, meta: "", kind: "xlsx", filename: `${id}.xlsx`, built_at: "2026-10-27" });
  const { rerender } = render(<FilesCard domain="x.example" files={[file("a"), file("b")]} />);
  expect(screen.queryByRole("link", { name: /Download selected/ })).toBeNull(); // no pre-ticked download
  expect(screen.getByText("Download selected (0)").getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(screen.getByRole("checkbox", { name: /^a/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /^b/ }));
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
  expect((screen.getByRole("button", { name: "Research this topic" }) as HTMLButtonElement).disabled).toBe(true);
});

// --- the Research board ----------------------------------------------------------------------

const item = {
  job_id: "11111111-1111-1111-1111-111111111111", company: "Acme", domain: "acme.example", kind: "New research", scope: "Everything",
  topic: null, label: "Step 2 of 8", open: true, status: "running", run_id: "r1", run_status: "RUNNING", can_stop: true,
  created_at: null, started_at: null, finished_at: null, findings: 0, searches: 0, steps_done: 1, steps_total: 8, step: null, tokens: 0,
};
const live = { as_of: "2026-10-27T00:00:00Z", detail: null, running: [item], recent: [] } as unknown as ResearchLive;

test("the board: Show steps names its company, only the connection state is announced, Start opens the pop-up", () => {
  render(<ResearchBoard initial={live} estimate={estimate} />);
  const show = screen.getByRole("button", { name: "Show steps: Acme" });
  fireEvent.click(show);
  expect(replace).toHaveBeenCalledWith(`/research?r=${item.job_id}`, { scroll: false });
  const statuses = screen.getAllByRole("status");
  expect(statuses.map((s) => s.textContent)).toContain("Connecting");
  // the running card carries its progress, without tokens or findings counts
  expect(screen.getByRole("progressbar", { name: "Acme: 1 of 8 steps done" })).toBeTruthy();
  expect(document.body.textContent).not.toMatch(/tokens/i);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Start new research" }));
  const dialog = screen.getByRole("dialog", { name: "Start new research" });
  expect(within(dialog).getByRole("textbox", { name: "Company name" })).toBeTruthy();
});

test("the board's load failure reloads the page instead of a client navigation that changes nothing", () => {
  render(<ResearchBoard initial={null} estimate={estimate} />);
  const reload = screen.getByRole("link", { name: "Refresh" });
  expect(reload.getAttribute("href")).toBe("/research");
  // starting a research still works while the board cannot be read
  expect(screen.getByRole("button", { name: "Start new research" })).toBeTruthy();
});

test("the board: a finished research says how it ended, offers Resume once, and opens its steps under its row", () => {
  const ended = { ...item, open: false, can_stop: false, can_resume: true, status: "failed", run_status: "FAILED", finished_at: "2026-10-27T00:00:00Z" };
  const detail = {
    job_id: ended.job_id, company: "Acme", domain: "acme.example", label: "Stopped", reason: "The source was unreachable.", steps: [], findings: 0, searches: 0,
    pages_read: 0, tokens: 0, findings_by_kind: [], latest_findings: [], withheld: 0,
  };
  const { unmount } = render(<ResearchBoard initial={{ ...live, running: [], recent: [ended], detail } as unknown as ResearchLive} estimate={estimate} />);
  expect(screen.getAllByRole("button", { name: "Resume research" })).toHaveLength(1);
  expect(screen.getByText("Did not finish")).toBeTruthy();
  const opened = screen.getByRole("region", { name: "Acme" });
  expect(within(opened).getByText("Reason: The source was unreachable.")).toBeTruthy();
  expect(screen.getByText("Nothing is running")).toBeTruthy();
  // Hide steps closes it without leaving the page
  fireEvent.click(screen.getByRole("button", { name: "Hide steps: Acme" }));
  expect(screen.queryByRole("region", { name: "Acme" })).toBeNull();
  unmount();
  render(<ResearchBoard initial={{ ...live, running: [], recent: [ended], detail: null } as unknown as ResearchLive} estimate={estimate} />);
  // the row itself carries Resume, so it is there with no research opened
  expect(screen.getAllByRole("button", { name: "Resume research" })).toHaveLength(1);
  expect(screen.queryByRole("region", { name: "Acme" })).toBeNull();
});
