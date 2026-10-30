// @vitest-environment jsdom
// The archive pages: an archived copy shown like a web archive (banner, original link, fields,
// platform sentences, text with its line breaks, Download JSON), the index of archived items, the
// navigation entry, and the archived-link address rewrite. SYNTHETIC data only.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { ArchiveList, ArchivedCopy } from "@/lib/gen/api.gen";
import { ownHref } from "@/lib/render";

const serverGet = vi.fn();
vi.mock("@/lib/server-api", async (original) => ({
  ...(await original<typeof import("@/lib/server-api")>()),
  serverGet: (...args: unknown[]) => serverGet(...args),
}));
vi.mock("next/navigation", () => ({ redirect: (to: string) => { throw new Error(`redirect:${to}`); } }));

vi.mock("@/components/accounts/FilesCard", () => ({ FilesCard: () => <div /> }));
vi.mock("@/components/accounts/UploadedFilesCard", () => ({ UploadedFilesCard: () => <div /> }));
vi.mock("@/components/accounts/AccountAsk", () => ({ AccountAsk: () => <div /> }));
vi.mock("@/components/accounts/ResumeResearch", () => ({ ResumeResearch: () => <div /> }));
vi.mock("@/components/accounts/ResearchProgress", () => ({ ResearchProgress: () => <div /> }));

import { ServerApiError } from "@/lib/server-api";
import AccountWorkspacePage from "@/app/(client)/accounts/[domain]/page";
import { ArchivedCopyView, ArchiveIndexView, OLD_AFTER_DAYS } from "@/components/accounts/ArchiveView";
import ArchivePage from "@/app/(client)/accounts/[domain]/archive/page";

afterEach(cleanup);

const URL_ = "https://jobs.synthetic-acme.example/p1?id=7&x=%3Cb%3E";
const NOW = new Date("2026-10-29T12:00:00Z");

const RECORD = {
  schema: "posting-archive/1",
  url: URL_,
  title: "Platform Engineer",
  requisition: "R-1001",
  location: "Springfield, US",
  status_at_capture: "closed",
  first_seen: "2026-09-01",
  last_seen: "2026-10-01",
  seniority: "mid_senior",
  class: "Build role",
  platforms: ["ServiceNow", "Workday"],
  platform_sentences: [{ platform: "ServiceNow", text: "Build ServiceNow flows." }],
  description: "Line one\nLine two",
  source: "6th agents",
};

function copy(over: Partial<ArchivedCopy> = {}): ArchivedCopy {
  return { url: URL_, kind: "posting", archived_at: "2026-10-20T09:00:00+00:00", title: "Platform Engineer", text: "Line one\nLine two", record: RECORD, ...over };
}

test("a posting copy reads like an archive page: banner, original link, fields, sentences, text", () => {
  render(<ArchivedCopyView copy={copy()} domain="synthetic-acme.example" now={NOW} />);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Platform Engineer");
  const banner = screen.getByRole("note");
  expect(banner.textContent).toContain("Archived copy · captured 20 October 2026");
  const original = within(banner).getByRole("link", { name: "Open original (may no longer exist)" });
  expect(original.getAttribute("href")).toBe(URL_);
  expect(original.getAttribute("rel")).toBe("noopener noreferrer");
  expect(original.getAttribute("target")).toBe("_blank");
  const grid = document.querySelector("dl.arc-fields")!;
  const field = (label: string) => within(grid as HTMLElement).getByText(label).nextElementSibling!.textContent;
  expect(field("Requisition")).toBe("R-1001");
  expect(field("Location")).toBe("Springfield, US");
  expect(field("Status when captured")).toBe("Closed");
  expect(field("First seen")).toBe("1 September 2026");
  expect(field("Last seen")).toBe("1 October 2026");
  expect(field("Seniority")).toBe("Mid senior");
  expect(field("Class")).toBe("Build role");
  expect(field("Platforms named")).toBe("ServiceNow, Workday");
  expect(screen.getByText("Build ServiceNow flows.", { exact: false }).textContent).toContain("ServiceNow");
  // the description keeps its line breaks through a class, never an inline style
  const text = document.querySelector(".arc-text")!;
  expect(text.textContent).toBe("Line one\nLine two");
  expect(document.querySelectorAll("[style]")).toHaveLength(0);
  expect(document.querySelectorAll("style")).toHaveLength(0);
  // a closed posting carries the quiet note
  expect(screen.getByText(/closed when it was captured/)).toBeTruthy();
});

test("Download JSON is a same-origin download of the record, and Back goes to Job Postings", () => {
  render(<ArchivedCopyView copy={copy()} domain="synthetic-acme.example" now={NOW} />);
  const download = screen.getByRole("link", { name: "Download JSON" });
  const href = download.getAttribute("href")!;
  expect(href.startsWith("/v1/accounts/synthetic-acme.example/archive?")).toBe(true);
  const q = new URLSearchParams(href.split("?")[1]);
  expect(q.get("url")).toBe(URL_);
  expect(q.get("download")).toBe("true");
  expect(download.hasAttribute("download")).toBe(true);
  const backs = screen.getAllByRole("link", { name: /Back to Job Postings/ });
  expect(backs.length).toBeGreaterThan(0);
  for (const b of backs) expect(b.getAttribute("href")).toBe("/accounts/synthetic-acme.example/jobs");
});

test("an open posting captured long ago says the original may have changed; a recent one is quiet", () => {
  const open = { ...RECORD, status_at_capture: "open" };
  const old = new Date(NOW.getTime() + (OLD_AFTER_DAYS + 1) * 86_400_000);
  const { rerender } = render(<ArchivedCopyView copy={copy({ record: open })} domain="d.example" now={old} />);
  expect(screen.getByText(/The original may have changed since/)).toBeTruthy();
  rerender(<ArchivedCopyView copy={copy({ record: open })} domain="d.example" now={NOW} />);
  expect(screen.queryByText(/may have changed/)).toBeNull();
  expect(screen.queryByText(/closed when it was captured/)).toBeNull();
});

test("hostile text is shown as text and an unsafe original address is not linked", () => {
  const hostile = "<script>window.__pwned = 1</script><img src=x onerror=alert(1)>";
  const dirty = copy({
    url: "javascript:alert(1)",
    title: hostile,
    text: hostile,
    record: { ...RECORD, title: hostile, requisition: hostile, location: hostile, description: hostile, platform_sentences: [{ platform: hostile, text: hostile }] },
  });
  const { container } = render(<ArchivedCopyView copy={dirty} domain="d.example" now={NOW} />);
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toContain("<script>window.__pwned = 1</script>");
  expect(screen.queryByRole("link", { name: /Open original/ })).toBeNull();
  expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined();
});

test("a page or profile copy shows its title and stored text the same way, with no posting fields", () => {
  const page = copy({ kind: "page", url: "https://synthetic-acme.example/news/a", title: "News item", text: "Para one.\nPara two.", record: null });
  render(<ArchivedCopyView copy={page} domain="synthetic-acme.example" now={NOW} />);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("News item");
  expect(document.querySelector(".arc-fields")).toBeNull();
  expect(document.querySelector(".arc-text")!.textContent).toBe("Para one.\nPara two.");
  expect(screen.getAllByRole("link", { name: /Back to the archive/ })[0]!.getAttribute("href")).toBe("/accounts/synthetic-acme.example/archive");
});

test("an older plain-text posting copy (no record) still opens as stored text", () => {
  render(<ArchivedCopyView copy={copy({ record: null, text: "Old text copy" })} domain="d.example" now={NOW} />);
  expect(document.querySelector(".arc-text")!.textContent).toBe("Old text copy");
  expect(document.querySelector(".arc-fields")).toBeNull();
});

const LIST: ArchiveList = {
  items: [
    { url: "https://jobs.synthetic-acme.example/p1", title: "Platform Engineer", kind: "posting", archived_at: "2026-10-20T09:00:00+00:00", status_at_capture: "closed", location: "Springfield, US", requisition: "R-1001" },
    { url: "https://synthetic-acme.example/news/a?x=1&y=2", title: null, kind: "page", archived_at: "2026-10-19T09:00:00+00:00" },
  ],
  total: 120, offset: 0, limit: 50, next_offset: 2,
};

test("the index lists title, kind, captured, status and location with View and original links", () => {
  render(<ArchiveIndexView list={LIST} domain="synthetic-acme.example" name="Synthetic Acme" kind="" showMore="/accounts/synthetic-acme.example/archive?limit=100" />);
  const table = screen.getByRole("table");
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Title", "Kind", "Captured", "Status when captured", "Location", "Links"]);
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);
  const first = within(rows[0]!);
  expect(first.getByText("Platform Engineer")).toBeTruthy();
  expect(first.getByText("Job posting")).toBeTruthy();
  expect(first.getByText("20 October 2026")).toBeTruthy();
  expect(first.getByText("Closed")).toBeTruthy();
  expect(first.getByText("Springfield, US")).toBeTruthy();
  expect(first.getByRole("link", { name: "View" }).getAttribute("href")).toBe("/accounts/synthetic-acme.example/archive?url=https%3A%2F%2Fjobs.synthetic-acme.example%2Fp1");
  expect(first.getByRole("link", { name: "original" }).getAttribute("href")).toBe("https://jobs.synthetic-acme.example/p1");
  // a copy with no title lists its address; the address is encoded whole in the View link
  const second = within(rows[1]!);
  expect(second.getByText("https://synthetic-acme.example/news/a?x=1&y=2")).toBeTruthy();
  expect(second.getByRole("link", { name: "View" }).getAttribute("href")).toBe("/accounts/synthetic-acme.example/archive?url=https%3A%2F%2Fsynthetic-acme.example%2Fnews%2Fa%3Fx%3D1%26y%3D2");
  expect(screen.getByText("Showing 2 of 120")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Show more" }).getAttribute("href")).toBe("/accounts/synthetic-acme.example/archive?limit=100");
});

test("an empty archive says so and offers no Show more", () => {
  render(<ArchiveIndexView list={{ items: [], total: 0, offset: 0, limit: 50, next_offset: null }} domain="d.example" name="D" kind="posting" showMore={null} />);
  expect(screen.getByText("Nothing archived yet")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Show more" })).toBeNull();
  expect(screen.getByRole("link", { name: "Job postings" }).getAttribute("aria-current")).toBe("page");
});

test("the page reads the copy with ?url= and the index without, and says so when there is none", async () => {
  serverGet.mockClear();
  serverGet.mockResolvedValue(copy());
  const { container } = render(await ArchivePage({ params: Promise.resolve({ domain: "synthetic-acme.example" }), searchParams: Promise.resolve({ url: URL_ }) }));
  expect(serverGet.mock.calls[0]![0]).toBe(`/v1/accounts/synthetic-acme.example/archive?${new URLSearchParams({ url: URL_ }).toString()}`);
  expect(container.textContent).toContain("Archived copy · captured 20 October 2026");
  cleanup();

  serverGet.mockClear();
  serverGet.mockResolvedValue(LIST);
  const index = render(await ArchivePage({ params: Promise.resolve({ domain: "synthetic-acme.example" }), searchParams: Promise.resolve({ kind: "posting" }) }));
  expect(serverGet.mock.calls[0]![0]).toBe("/v1/accounts/synthetic-acme.example/archive/list?limit=50&kind=posting");
  // 2 of 120 shown: Show more grows the page by 50 rows
  expect(index.getByRole("link", { name: "Show more" }).getAttribute("href")).toBe("/accounts/synthetic-acme.example/archive?kind=posting&limit=100");
  cleanup();

  serverGet.mockResolvedValue(null);
  const none = render(await ArchivePage({ params: Promise.resolve({ domain: "d.example" }), searchParams: Promise.resolve({ url: "https://gone.example/x" }) }));
  expect(none.getByText("No archived copy")).toBeTruthy();
  cleanup();

  serverGet.mockRejectedValue(new ServerApiError(503, "ref"));
  const down = render(await ArchivePage({ params: Promise.resolve({ domain: "d.example" }), searchParams: Promise.resolve({}) }));
  expect(down.getByText("The archive could not be loaded")).toBeTruthy();
});

test("a bad kind or limit falls back to the first page of everything", async () => {
  serverGet.mockClear();
  serverGet.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 50, next_offset: null });
  render(await ArchivePage({ params: Promise.resolve({ domain: "d.example" }), searchParams: Promise.resolve({ kind: "x'; drop", limit: "-5", offset: "abc" }) }));
  expect(serverGet.mock.calls[0]![0]).toBe("/v1/accounts/d.example/archive/list?limit=50");
});

test("the archived link of a delivered file opens the website's own page whatever origin it carries", () => {
  const mine = "/accounts/x/archive?url=https%3A%2F%2Fjobs.example%2Fp1";
  expect(ownHref("https://web.example/accounts/x/archive?url=https%3A%2F%2Fjobs.example%2Fp1")).toBe(mine);
  expect(ownHref("https://web.example/v1/accounts/x/archive?url=https%3A%2F%2Fjobs.example%2Fp1")).toBe(mine); // the older API path
  expect(ownHref("https://jobs.example/p1")).toBe("https://jobs.example/p1"); // an original is left alone
  expect(ownHref("https://web.example/accounts/x/archive")).toBe("https://web.example/accounts/x/archive"); // no address, no rewrite
  expect(ownHref(null)).toBeNull();
});

test("the account's Deep dive navigation lists Archive beside the other tables", async () => {
  serverGet.mockClear();
  serverGet.mockResolvedValue({
    id: null, domain: "synthetic-acme.example", name: "Synthetic Acme", subline: "x", first_scan: true, files: [], findings: [],
    narrative: null, changes: null, latest_run: null, open_job: null, stopped_run: null, gaps: [],
    counts: { signals: 1, people: 2, jobs: 3, tech: 0, news: 0, mail_drive: 0, sources: 4, searches: 0, coverage_done: 0, coverage_total: 0, facts: 0 },
  });
  render(await AccountWorkspacePage({ params: Promise.resolve({ domain: "synthetic-acme.example" }) }));
  const rail = screen.getByRole("navigation", { name: "Deep dive" });
  const labels = within(rail).getAllByRole("link").map((a) => a.textContent!.replace(/\d+$/, ""));
  expect(labels).toContain("Job postings");
  expect(labels).toContain("Archive");
  expect(within(rail).getByRole("link", { name: "Archive" }).getAttribute("href")).toBe("/accounts/synthetic-acme.example/archive");
});

const NEWS = {
  schema: "news-archive/1", url: "https://www.press.synthetic.example/r/1", title: "Synthetic Acme names a CIO", publisher: "press.synthetic.example",
  published_date: "2026-09-30", category: "leadership change", summary: "Acme appointed a CIO.", article_text: "Para one.\nPara two.", source: "6th agents",
};

test("a news copy shows headline, publisher, category, published date, summary and the article text", () => {
  render(<ArchivedCopyView copy={copy({ kind: "page", url: NEWS.url, title: NEWS.title, text: NEWS.article_text, record: NEWS })} domain="synthetic-acme.example" now={NOW} />);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Synthetic Acme names a CIO");
  expect(screen.getByText("press.synthetic.example · Leadership change")).toBeTruthy();
  const strip = screen.getByRole("note");
  expect(within(strip).getByText("News article")).toBeTruthy();
  expect(within(strip).getByText("Published")).toBeTruthy();
  expect(within(strip).getByText("30 September 2026")).toBeTruthy();
  expect(within(strip).getByText("Captured")).toBeTruthy();
  expect(screen.getByText("Acme appointed a CIO.").className).toBe("arc-lead");
  expect(screen.getByRole("heading", { name: "Article text" })).toBeTruthy();
  expect(document.querySelector(".arc-text")!.textContent).toBe("Para one.\nPara two.");
  expect(screen.getAllByRole("link", { name: "Back to News" })[0]!.getAttribute("href")).toBe("/accounts/synthetic-acme.example/news");
  expect(document.querySelector(".arc-fields")).toBeNull();
});

test("the capture strip says Open or Closed when captured for a posting", () => {
  const { rerender } = render(<ArchivedCopyView copy={copy({ record: { ...RECORD, status_at_capture: "open" } })} domain="d.example" now={NOW} />);
  expect(within(screen.getByRole("note")).getByText("Open when captured")).toBeTruthy();
  rerender(<ArchivedCopyView copy={copy()} domain="d.example" now={NOW} />);
  expect(within(screen.getByRole("note")).getByText("Closed when captured")).toBeTruthy();
  expect(within(screen.getByRole("note")).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
    "First seen1 September 2026", "Last seen1 October 2026", "Captured20 October 2026",
  ]);
});

test("a profile shows name, title, headline and location; a detection its technology and dates", () => {
  const profile = copy({
    kind: "profile", url: "https://www.linkedin.com/in/jane-doe", title: "Jane Doe", text: "Jane Doe\nCIO",
    record: { schema: "profile-archive/1", name: "Jane Doe", job_title: "Chief Information Officer", headline: "Technology leader", location: "Springfield", text: "Jane Doe\nCIO" },
  });
  const { rerender } = render(<ArchivedCopyView copy={profile} domain="d.example" now={NOW} />);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Jane Doe");
  expect(screen.getByText("Chief Information Officer", { selector: ".sub" })).toBeTruthy();
  const grid = document.querySelector("dl.arc-fields") as HTMLElement;
  expect(within(grid).getByText("Headline").nextElementSibling!.textContent).toBe("Technology leader");
  expect(within(grid).getByText("Location").nextElementSibling!.textContent).toBe("Springfield");
  const detection = copy({
    kind: "detection", url: "https://synthetic-acme.example/stack", title: "Workday", text: "Workday",
    record: { schema: "detection-archive/1", technology: "Workday", first_seen: "2025-01-02", last_seen: "2026-09-30", sources: 3, text: "Workday" },
  });
  rerender(<ArchivedCopyView copy={detection} domain="d.example" now={NOW} />);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Workday");
  const g2 = document.querySelector("dl.arc-fields") as HTMLElement;
  expect(within(g2).getByText("First seen").nextElementSibling!.textContent).toBe("2 January 2025");
  expect(within(g2).getByText("Sources").nextElementSibling!.textContent).toBe("3");
  expect(within(screen.getByRole("note")).getByText("Last seen")).toBeTruthy();
});

test("a generic page shows its publisher and date under the title, then its text", () => {
  const page = copy({ kind: "page", url: "https://synthetic-acme.example/team", title: "Our team", text: "Body", record: { schema: "page-archive/1", title: "Our team", publisher: "synthetic-acme.example", published_date: "2026-08-01", text: "Body" } });
  render(<ArchivedCopyView copy={page} domain="d.example" now={NOW} />);
  expect(screen.getByText("synthetic-acme.example · 1 August 2026")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Stored text" })).toBeTruthy();
});

test("the index names a news article by its schema and a posting row shows its requisition", () => {
  const list: ArchiveList = {
    items: [
      { url: "https://www.press.synthetic.example/r/1", title: "Acme names a CIO", kind: "page", archived_at: "2026-10-20T09:00:00+00:00", record_schema: "news-archive/1" },
      { url: "https://jobs.synthetic-acme.example/p2", title: "Engineer", kind: "posting", archived_at: "2026-10-19T09:00:00+00:00", status_at_capture: "open", requisition: "R-9", record_schema: "posting-archive/1" },
    ],
    total: 2, offset: 0, limit: 50, next_offset: null,
  };
  render(<ArchiveIndexView list={list} domain="d.example" name="D" kind="" showMore={null} />);
  const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
  expect(within(rows[0]!).getByText("News article")).toBeTruthy();
  expect(within(rows[1]!).getByText("Open")).toBeTruthy();
  expect(within(rows[1]!).getByText("R-9")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Pages and news" })).toBeTruthy();
});

test("the first column opens the copy, so a phone that scrolls the table sideways still has it", () => {
  render(<ArchiveIndexView list={LIST} domain="synthetic-acme.example" name="Synthetic Acme" kind="" showMore={null} />);
  const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
  const title = within(rows[0]!).getAllByRole("cell")[0]!;
  expect(within(title).getByRole("link", { name: "Platform Engineer" }).getAttribute("href")).toBe("/accounts/synthetic-acme.example/archive?url=https%3A%2F%2Fjobs.synthetic-acme.example%2Fp1");
});
