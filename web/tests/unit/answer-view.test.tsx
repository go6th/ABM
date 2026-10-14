// @vitest-environment jsdom
// Model output is untrusted: the answer view renders its text as text, and a source opens as a
// link only when its address is an absolute http(s) URL (a scheme-relative "//host" is refused).
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { AnswerView, dayMonth } from "@/components/ask/AnswerView";
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
