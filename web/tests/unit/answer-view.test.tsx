// @vitest-environment jsdom
// Model output is untrusted: the answer view renders its text as text, and a source opens as a
// link only when its address is an absolute http(s) URL (a scheme-relative "//host" is refused).
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { AnswerView, dayMonth, UsedPanel } from "@/components/ask/AnswerView";
import type { ChatAnswer } from "@/lib/gen/api.gen";

afterEach(cleanup);

function answer(url: string | null, text = "Plain text [1]"): ChatAnswer {
  return {
    account_domain: null,
    account_name: null,
    answered: true,
    followups: [],
    items: null,
    offer: null,
    paragraphs: [[{ t: "text", v: text }, { t: "cite", v: "1" }]],
    sources: [{ account_domain: null, account_name: null, date: null, kind: "web", meta: "Careers page", n: 1, title: "Cloud lead", url }],
    used: { mail_drive: "not_connected", web: "not_used", workspace: "used" },
  };
}

function view(a: ChatAnswer) {
  return render(<AnswerView answer={a} openSource={1} onOpenSource={() => undefined} offerDismissed={false} onDismissOffer={() => undefined} onFollowUp={() => undefined} onPickAccount={() => undefined} busy={false} />);
}

test("hostile text renders inert: no script, no image, no handler", () => {
  const { container } = view(answer(null, "<script>window.pwned = 1</script><img src=x onerror=alert(1)>"));
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container.innerHTML).not.toContain("<img");
  expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
});

test("an https source opens as a link that cannot reach back to the page", () => {
  view(answer("https://careers.example/1"));
  const link = screen.getByRole("link", { name: "Open source" });
  expect(link.getAttribute("href")).toBe("https://careers.example/1");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(link.getAttribute("target")).toBe("_blank");
});

test.each(["//evil.example/x", "javascript:alert(1)", "/\\evil.example", "data:text/html,x", ""])("the source address %j is not made a link", (url) => {
  view(answer(url));
  expect(screen.queryByRole("link", { name: "Open source" })).toBeNull();
});

test("day and month read plainly and a bad date reads as nothing", () => {
  expect(dayMonth("2026-10-06")).toBe("6 Oct");
  expect(dayMonth("not a date")).toBe("");
  expect(dayMonth(null)).toBe("");
});

test("a cited source reads 6th and its site and opens the original page from the answer", () => {
  const a = answer("https://www.linkedin.com/jobs/view/1");
  a.sources[0]!.label = "6th / LinkedIn";
  const { container } = view(a);
  const inline = container.querySelector(".reply .md a.src-link");
  expect(inline?.textContent).toBe("6th / LinkedIn");
  expect(inline?.getAttribute("href")).toBe("https://www.linkedin.com/jobs/view/1");
  expect(inline?.getAttribute("target")).toBe("_blank");
  // the source card links the same way
  expect(screen.getByRole("link", { name: "6th / LinkedIn" }).getAttribute("href")).toBe("https://www.linkedin.com/jobs/view/1");
});

test("a source with no web page (a provider's own page) has no link in the answer", () => {
  const a = answer(null);
  a.sources[0]!.label = "6th agents";
  const { container } = view(a);
  expect(container.querySelector("a.src-link")).toBeNull();
});

// --- an Ask citation of a deep-dive table opens the sheet (owner, 2026-11-04) ------------------

function sheetAnswer(match: { match_col: string; match: string } | null): ChatAnswer {
  const a = answer(null, "12 openings in San Francisco [1]");
  a.sources[0] = {
    account_domain: "holdings.example", account_name: "Synthetic Holdings", date: null, kind: "file",
    meta: match ? `Filtered: Location contains ${match.match}` : "Internal · 644 rows, queried whole",
    n: 1, title: "Job postings sheet", url: null, label: null,
    sheet: { key: "jobs", ...(match ?? {}) },
  };
  return a;
}

test("a cited sheet opens the deep dive with its match encoded, in the answer and the source card", () => {
  const a = sheetAnswer({ match_col: "location", match: "San Francisco & Bay/Area" });
  const { container } = view(a);
  const href = "/accounts/holdings.example/jobs?match_col=location&match=San%20Francisco%20%26%20Bay%2FArea";
  const inline = container.querySelector(".reply .md a.src-link");
  expect(inline?.textContent).toBe("Open in the deep dive");
  expect(inline?.getAttribute("href")).toBe(href);
  // an in-app page: no new tab, no external rel
  expect(inline?.getAttribute("target")).toBeNull();
  const card = container.querySelector(".src-card a.link");
  expect(card?.textContent).toBe("Open in the deep dive");
  expect(card?.getAttribute("href")).toBe(href);
  expect(container.querySelector(".src-card")?.textContent).toContain("Filtered: Location contains San Francisco & Bay/Area");
});

test("a cited sheet without a match opens the whole sheet; a sheet without an account has no link", () => {
  const { container, unmount } = view(sheetAnswer(null));
  expect(container.querySelector(".reply .md a.src-link")?.getAttribute("href")).toBe("/accounts/holdings.example/jobs");
  unmount();
  const orphan = sheetAnswer(null);
  orphan.sources[0]!.account_domain = null;
  const again = view(orphan);
  expect(again.container.querySelector("a.src-link")).toBeNull();
});

test("a cited cell of the structured answer and the used panel open the same sheet", () => {
  const a = sheetAnswer({ match_col: "location", match: "San Francisco" });
  a.structured = {
    headline: "12 openings [1]",
    stats: [],
    sections: [{ kind: "table", title: "Openings", columns: ["City", "Openings"], rows: [["San Francisco [1]", "12"]] }],
  };
  const { container } = view(a);
  const href = "/accounts/holdings.example/jobs?match_col=location&match=San%20Francisco";
  const links = [...container.querySelectorAll(".reply a.src-link")].map((l) => l.getAttribute("href"));
  expect(links.length).toBeGreaterThanOrEqual(2); // the headline and the table cell
  expect(new Set(links)).toEqual(new Set([href]));
  cleanup();
  const used = render(<UsedPanel answer={a} onOpenSource={() => undefined} />);
  const link = used.getByRole("link", { name: /Open in the deep dive: source 1/ });
  expect(link.getAttribute("href")).toBe(href);
  expect(used.container.textContent).toContain("Job postings sheet");
});
