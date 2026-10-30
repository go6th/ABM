// @vitest-environment jsdom
// A delivered file's table on screen: the file's headers in the file's order, a link column
// showing the file's own text ("Open profile") and opening the address, the source code opening
// its source, and the caption naming the file's sheet and its row order; two link columns in one
// row, the file's cell fills as classes, and a long cell cut by the page with the whole cell one
// request away.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Sheet } from "@/lib/gen/api.gen";
import { rowKeys, SheetView } from "@/components/accounts/SheetView";

afterEach(() => cleanup());

const sheet: Sheet = {
  key: "people",
  title: "People",
  file: "People list.xlsx",
  scope: "The table of your delivered files, row for row.",
  footnote: "Only Confirmed facts should be said to a prospect.",
  private: false,
  account_name: "Synthetic Ltd",
  from_file: true,
  sheet_name: "People",
  columns: [
    { key: "number", label: "No.", sortable: true, kind: "num" },
    { key: "name", label: "Name", sortable: true, kind: "strong" },
    { key: "profile_url", label: "LinkedIn", sortable: false, kind: "url" },
    { key: "note", label: "Note", sortable: true, kind: "text" },
    { key: "source_code", label: "Source", sortable: true, kind: "code" },
  ],
  filters: [{ key: "all", label: "All", count: 1 }],
  filter: "all",
  sort: "_file",
  dir: "asc",
  total: 1,
  offset: 0,
  rows: [
    {
      n: 1,
      cells: { number: "1", name: "Synthetic Director", profile_url: "Open profile", note: "", source_code: "S-0001" },
      links: { profile_url: "https://www.linkedin.com/in/synthetic-director" },
      link: "https://example.com/team",
      link_label: "6th / example.com",
    },
  ],
};

test("a delivered file's table shows its headers, link text and source links", () => {
  render(<SheetView sheet={sheet} domain="synthetic.example" base="/accounts/synthetic.example/people" moreHref={null} />);
  const table = screen.getByRole("table");
  const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent?.replace(/[▲▼]/g, "").trim());
  expect(headers).toEqual(["Row", "No.", "Name", "LinkedIn", "Note", "Source"]);
  const profile = within(table).getByRole("link", { name: "Open profile" });
  expect(profile.getAttribute("href")).toBe("https://www.linkedin.com/in/synthetic-director");
  expect(within(table).getByRole("link", { name: "S-0001" }).getAttribute("href")).toBe("https://example.com/team");
  expect(screen.getByText(/People list\.xlsx · People · 1 rows/)).toBeTruthy();
  expect(screen.getByText(/In the file's order\./)).toBeTruthy();
});

test("a link with an archived copy shows the copy beside it, a link without one does not", () => {
  const archived: Sheet = {
    ...sheet,
    rows: [
      {
        ...sheet.rows[0]!,
        archives: {
          profile_url: { href: "/accounts/synthetic.example/archive?url=https%3A%2F%2Fwww.linkedin.com%2Fin%2Fsynthetic-director", archived_at: "2026-10-27T09:00:00+00:00" },
          link: { href: "https://evil.example/not-ours", archived_at: "2026-10-27T09:00:00+00:00" },
        },
      },
    ],
  };
  render(<SheetView sheet={archived} domain="synthetic.example" base="/accounts/synthetic.example/people" moreHref={null} />);
  const table = screen.getByRole("table");
  const copies = within(table).getAllByRole("link", { name: "archived" });
  // the profile's copy opens the website's own archive page; the source link's copy pointed off
  // the origin and is not shown
  expect(copies).toHaveLength(1);
  expect(copies[0]!.getAttribute("href")).toBe("/accounts/synthetic.example/archive?url=https%3A%2F%2Fwww.linkedin.com%2Fin%2Fsynthetic-director");
  expect(copies[0]!.getAttribute("title")).toBe("Archived copy, captured 2026-10-27");
  expect(within(table).getByRole("link", { name: "Open profile" })).toBeTruthy();
});

// --- the audit of 2026-10-29: two link columns, fills, cut cells ------------------------------

const jobs: Sheet = {
  ...sheet,
  key: "jobs",
  title: "Job postings",
  file: "Job postings.xlsx",
  sheet_name: "Jobs",
  columns: [
    { key: "c1", label: "Title", sortable: true, kind: "text" },
    { key: "c2", label: "Priority", sortable: true, kind: "text" },
    { key: "c3", label: "Full description", sortable: false, kind: "long" },
    { key: "c4", label: "Link", sortable: false, kind: "url" },
    { key: "c5", label: "Archived link", sortable: false, kind: "url" },
  ],
  rows: [
    {
      n: 1,
      cells: { c1: "Salesforce Administrator", c2: "A", c3: "Teams work in parallel…", c4: "Open posting", c5: "Open archived copy" },
      links: { c4: "https://jobs.example/posting-1", c5: "https://web.example/v1/accounts/x/archive?url=https%3A%2F%2Fjobs.example%2Fposting-1" },
      fills: { c2: "priority_a", c1: "not-a-token" },
      cut: { c3: 12345 },
      link: null,
    },
  ],
};

test("two link columns in one row both open their own address with the file's own text", () => {
  render(<SheetView sheet={jobs} domain="synthetic.example" base="/accounts/synthetic.example/jobs" moreHref={null} />);
  const table = screen.getByRole("table");
  expect(within(table).getByRole("link", { name: "Open posting" }).getAttribute("href")).toBe("https://jobs.example/posting-1");
  // the file's archived link carries the public origin of the day (or the older API path); the
  // table opens it as the website's own page, same origin, so the session is sent
  expect(within(table).getByRole("link", { name: "Open archived copy" }).getAttribute("href")).toBe("/accounts/x/archive?url=https%3A%2F%2Fjobs.example%2Fposting-1");
});

test("a cell's fill token becomes its class; an unknown token styles nothing", () => {
  render(<SheetView sheet={jobs} domain="synthetic.example" base="/accounts/synthetic.example/jobs" moreHref={null} />);
  const table = screen.getByRole("table");
  const priority = within(table).getByText("A").closest("td");
  expect(priority?.className).toBe("fill-priority-a");
  const title = within(table).getByText("Salesforce Administrator").closest("td");
  expect(title?.className ?? "").not.toMatch(/fill-/);
});

test("a cut cell offers the whole cell and fetches it from the cell route under the page's order", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ n: 1, col: "c3", text: "Teams work in parallel on the rollout. The whole text.", href: null, withheld: false }), { status: 200, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  try {
    render(<SheetView sheet={{ ...jobs, sort: "c1", dir: "desc" }} domain="synthetic.example" base="/accounts/synthetic.example/jobs" moreHref={null} />);
    const button = screen.getByRole("button", { name: "Show the whole Full description cell of row 1" });
    expect(button.textContent).toBe("Show all (12,345 characters)");
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByText("Teams work in parallel on the rollout. The whole text.")).toBeTruthy());
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toBe("/v1/accounts/synthetic.example/sheets/jobs/cell?filter=all&sort=c1&dir=desc&n=1&col=c3");
    expect(screen.queryByRole("button", { name: /Show the whole/ })).toBeNull();
  } finally {
    vi.unstubAllGlobals();
  }
});

test("a cut cell whose whole text cannot be fetched says so and keeps the page", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 503 })));
  try {
    render(<SheetView sheet={jobs} domain="synthetic.example" base="/accounts/synthetic.example/jobs" moreHref={null} />);
    fireEvent.click(screen.getByRole("button", { name: /Show the whole/ }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/could not be loaded/));
    expect(screen.getByText("Teams work in parallel…")).toBeTruthy();
  } finally {
    vi.unstubAllGlobals();
  }
});

test("the buttons of a file-shaped tab keep the view: chips, sort headings, Show more rows and the downloads", () => {
  const view: Sheet = {
    ...jobs,
    from_file: true,
    total: 7,
    filter: "status_open",
    sort: "c1",
    dir: "asc",
    filters: [
      { key: "all", label: "All", count: 12 },
      { key: "status_open", label: "Open", count: 7 },
      { key: "status_closed", label: "Closed", count: 5 },
    ],
  };
  render(<SheetView sheet={view} domain="synthetic.example" base="/accounts/synthetic.example/jobs" moreHref="/accounts/synthetic.example/jobs?limit=400" />);
  // chips: the open one is current; the others link to their own filter and keep the sort
  const open = screen.getByRole("link", { name: "Open · 7" });
  expect(open.getAttribute("aria-current")).toBe("true");
  const closed = screen.getByRole("link", { name: "Closed · 5" });
  expect(closed.getAttribute("href")).toBe("/accounts/synthetic.example/jobs?filter=status_closed&sort=c1&dir=asc");
  expect(screen.getByRole("link", { name: "All 12" }).getAttribute("href")).toBe("/accounts/synthetic.example/jobs?filter=all&sort=c1&dir=asc");
  // a sort heading flips its direction under the same filter; a link column is not a sort link
  const title = screen.getByRole("link", { name: /^Title/ });
  expect(title.getAttribute("href")).toBe("/accounts/synthetic.example/jobs?filter=status_open&sort=c1&dir=desc");
  expect(screen.queryByRole("link", { name: /^Archived link/ })).toBeNull();
  // the downloads and Show more rows carry the filter and the sort
  expect(screen.getByRole("link", { name: "Download CSV" }).getAttribute("href")).toBe("/v1/accounts/synthetic.example/sheets/jobs/csv?filter=status_open&sort=c1&dir=asc");
  expect(screen.getByRole("link", { name: "Download table as Excel" }).getAttribute("href")).toBe("/v1/accounts/synthetic.example/sheets/jobs/xlsx?filter=status_open&sort=c1&dir=asc");
  expect(screen.getByRole("link", { name: "Show more rows" }).getAttribute("href")).toBe("/accounts/synthetic.example/jobs?limit=400");
});

// --- fix wave C (2026-10-29): row identity, one number format, wrapping titles -----------------

function two(): Sheet {
  const cut = (n: number, title: string, text: string) => ({
    n,
    cells: { c1: title, c2: "A", c3: text, c4: "Open posting", c5: "" },
    links: { c4: `https://jobs.example/${title}` },
    cut: { c3: 5400 },
    link: null,
  });
  return { ...jobs, total: 2, rows: [cut(1, "Alpha", "Alpha description cut…"), cut(2, "Beta", "Beta description cut…")] };
}

test("an expanded cell stays with its own posting when the table is sorted again", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ n: 1, col: "c3", text: "ALPHA whole description", href: null }), { status: 200, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  try {
    const first = two();
    const { rerender } = render(<SheetView sheet={first} domain="synthetic.example" base="/accounts/synthetic.example/jobs" moreHref={null} />);
    fireEvent.click(screen.getAllByRole("button", { name: /Show the whole Full description cell of row 1/ })[0]!);
    await waitFor(() => expect(screen.getByText("ALPHA whole description")).toBeTruthy());
    // sorted the other way: Beta is now row 1 and Alpha row 2
    const flipped: Sheet = { ...first, dir: "desc", rows: [{ ...first.rows[1]!, n: 1 }, { ...first.rows[0]!, n: 2 }] };
    rerender(<SheetView sheet={flipped} domain="synthetic.example" base="/accounts/synthetic.example/jobs" moreHref={null} />);
    const rows = screen.getAllByRole("row").slice(1);
    // Beta (now first) must not show Alpha's whole description; Alpha keeps its own
    expect(within(rows[0]!).queryByText("ALPHA whole description")).toBeNull();
    expect(within(rows[0]!).getByText("Beta description cut…")).toBeTruthy();
    expect(within(rows[1]!).getByText("ALPHA whole description")).toBeTruthy();
  } finally {
    vi.unstubAllGlobals();
  }
});

test("two rows that print the same still get their own keys", () => {
  const same = { n: 1, cells: { a: "x" }, links: {}, link: null };
  expect(new Set(rowKeys([same, { ...same, n: 2 }])).size).toBe(2);
});

test("the number of a cut cell reads the same wherever it is rendered", () => {
  render(<SheetView sheet={two()} domain="synthetic.example" base="/accounts/synthetic.example/jobs" moreHref={null} />);
  expect(screen.getAllByRole("button", { name: /Show the whole/ })[0]!.textContent).toBe("Show all (5,400 characters)");
});

test("a long title wraps in its cell instead of widening the table", () => {
  const long: Sheet = { ...jobs, rows: [{ ...jobs.rows[0]!, cells: { ...jobs.rows[0]!.cells, c1: "Senior Salesforce Marketing Cloud Engineering and Platform Operations Manager, Global" } }] };
  render(<SheetView sheet={long} domain="synthetic.example" base="/accounts/synthetic.example/jobs" moreHref={null} />);
  const title = within(screen.getByRole("table")).getByText(/Senior Salesforce Marketing Cloud/).closest("td");
  expect(title?.className).toContain("wrap");
});
