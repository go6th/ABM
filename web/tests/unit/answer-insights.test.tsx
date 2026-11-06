// @vitest-environment jsdom
// An open question's answer (2026-11-03): numbered insights, each with its grade and one next
// step; the facts table the code built opens under "Sources" (collapsed); the model's thinking
// summary opens under "How this was worked out" and renders as text, never as HTML. No inline
// style reaches the DOM (the CSP forbids them).
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { AnswerStructured } from "@/components/ask/AnswerStructured";
import { AnswerView } from "@/components/ask/AnswerView";
import type { AnswerStructured as Structured, ChatAnswer } from "@/lib/gen/api.gen";

afterEach(cleanup);

const chip = (n: string) => <span className="cite static" data-cite={n}>{n}</span>;

const structured: Structured = {
  headline: "Three budget signals for the account [1].",
  stats: [],
  sections: [
    {
      kind: "insights",
      insights: [
        { conclusion: "A CRM program is selecting integrators now [1].", grade: "Confirmed", action: "Ask Client which waves are funded." },
        { conclusion: "ERP hiring shows skills demand, not budget size [2].", grade: "Hypothesis", action: "Ask how the go-live is staffed." },
        { conclusion: "New owners joined in the last six months [1].", grade: "Probable", action: "Map each owner's first 90 days." },
      ],
    },
    {
      kind: "table",
      title: "Facts this answer rests on",
      columns: ["Account", "Fact", "Source"],
      rows: [["Acme", "CRM program", "[1]"], ["Acme", "ERP postings", "[2]"]],
      collapsed: true,
    },
  ],
};

test("insights render numbered, graded and with one next step each", () => {
  const { container } = render(<AnswerStructured data={structured} cite={chip} />);
  const items = container.querySelectorAll(".sa-insights > li");
  expect(items).toHaveLength(3);
  expect(items[0]!.querySelector(".sa-grade")!.textContent).toBe("Confirmed");
  expect(items[0]!.querySelector(".sa-grade")!.className).toContain("confirmed");
  expect(items[1]!.querySelector(".sa-grade")!.className).toContain("hypothesis");
  expect(items[2]!.textContent).toContain("Next step: Map each owner's first 90 days.");
  expect(container.querySelectorAll("[data-cite]").length).toBeGreaterThanOrEqual(3);
  expect(container.querySelector("[style]")).toBeNull();
});

test("a collapsed facts table opens under Sources and starts closed", () => {
  const { container } = render(<AnswerStructured data={structured} cite={chip} />);
  const sources = container.querySelector("details.sa-sources") as HTMLDetailsElement;
  expect(sources).not.toBeNull();
  expect(sources.open).toBe(false);
  expect(sources.querySelector("summary")!.textContent).toBe("Sources: 2 facts this answer rests on");
  expect(sources.querySelectorAll("tbody tr")).toHaveLength(2);
  // the table's own title is not repeated inside the disclosure
  expect(sources.querySelector(".sa-title")).toBeNull();
});

test("the thinking summary opens under its own heading and renders as text", () => {
  const answer: ChatAnswer = {
    account_domain: null,
    account_name: null,
    answered: true,
    followups: [],
    items: null,
    offer: null,
    paragraphs: [[{ t: "text", v: "Three signals" }]],
    sources: [],
    used: { mail_drive: "not_connected", web: "not_used", workspace: "used" },
    thinking: "Start from the Tier 1 table.\n\n<img src=x onerror=alert(1)>",
  };
  const { container } = render(
    <AnswerView answer={answer} openSource={null} onOpenSource={() => undefined} offerDismissed={false} onDismissOffer={() => undefined} onFollowUp={() => undefined} onPickAccount={() => undefined} busy={false} />,
  );
  const box = container.querySelector("details.sa-thinking") as HTMLDetailsElement;
  expect(box.open).toBe(false);
  expect(screen.getByText("How this was worked out")).toBeTruthy();
  expect(box.textContent).toContain("Start from the Tier 1 table.");
  expect(container.querySelector("img")).toBeNull();
});
