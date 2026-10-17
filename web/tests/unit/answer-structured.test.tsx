// @vitest-environment jsdom
// The render_answer answer (2026-10-17): the code renders the model's strict JSON — headline
// card with its numbers, tables that carry EVERY row, callouts, steps. A column identical on
// every row folds into a badge by the title and leaves the table; rows identical in every cell
// merge with a ×N badge; dates never wrap; citations are chips; no raw [[…]] row marker and no
// inline style ever reaches the DOM (the CSP forbids them).
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { AnswerStructured as Structured } from "@/lib/gen/api.gen";
import { AnswerStructured, citedText } from "@/components/ask/AnswerStructured";

afterEach(cleanup);

const chip = (n: string) => <span className="cite static" data-cite={n}>{n}</span>;

function view(data: Structured) {
  return render(<AnswerStructured data={data} cite={chip} />);
}

const fixture: Structured = {
  headline: "**GSK**: no Salesforce-related posting opened since 1 March 2026 [1].",
  stats: [
    { label: "open since 1 Mar 2026", value: "0" },
    { label: "unique closed matches", value: "14" },
  ],
  sections: [
    {
      kind: "prose",
      title: "The finding",
      paragraphs: ["The strict filter returns 0 rows, so the status alone was relaxed [1]."],
    },
    {
      kind: "table",
      title: "Every matching posting",
      columns: ["Requisition", "Title", "Status", "First seen", "Source"],
      rows: [
        ["REQ-1", "Salesforce Architect", "Closed", "2026-03-04", "[1]"],
        ["REQ-2", "Integration Lead", "Closed", "5 Apr 2026", "[1]"],
        ["REQ-3", "Field Sales Rep", "Closed", "Apr 2026", "[2]"],
      ],
    },
    { kind: "callout", text: "All matches are closed roles [1].", tone: "warn" },
    { kind: "steps", title: "Next steps", items: ["Ask about successor postings [1]."] },
  ],
};

test("the headline, its numbers and every section render, citations as chips", () => {
  const { container } = view(fixture);
  const head = container.querySelector(".sa-head");
  expect(head?.querySelector(".sa-headline strong")?.textContent).toBe("GSK");
  expect(head?.querySelectorAll(".sa-stat").length).toBe(2);
  expect(head?.querySelector(".sa-stat b")?.textContent).toBe("0");
  expect(container.textContent).toContain("The finding");
  expect(container.textContent).toContain("The strict filter returns 0 rows");
  expect(container.querySelector("ol.sa-steps li")?.textContent).toContain("successor postings");
  // citations anywhere — headline, prose, table cells, callout, steps — are chips
  const chips = Array.from(container.querySelectorAll(".cite[data-cite]"));
  expect(chips.length).toBeGreaterThanOrEqual(6);
  expect(chips.every((c) => ["1", "2"].includes(c.getAttribute("data-cite") ?? ""))).toBe(true);
  expect(container.innerHTML).not.toContain("[[");
  expect(container.innerHTML).not.toContain("style=");
});

test("a table carries every row; a uniform column folds into a badge and leaves the table", () => {
  const { container } = view(fixture);
  const section = container.querySelectorAll(".sa-section")[1]!;
  expect(section.querySelector(".sa-title")?.textContent).toBe("Every matching posting");
  // Status was Closed on every row: it became a badge by the title, not a column
  const badges = Array.from(section.querySelectorAll(".sa-badge")).map((b) => b.textContent);
  expect(badges).toEqual(["Status: Closed"]);
  expect(Array.from(section.querySelectorAll("th")).map((th) => th.textContent)).toEqual([
    "Requisition",
    "Title",
    "First seen",
    "Source",
  ]);
  const body = section.querySelectorAll("tbody tr");
  expect(body.length).toBe(3); // every row, never a sample
  expect(body[0]?.textContent).toContain("REQ-1");
  expect(body[0]?.textContent).toContain("Salesforce Architect");
  // nothing in the body still says Closed
  expect(section.querySelector("tbody")?.textContent).not.toContain("Closed");
});

test("identical rows merge into one with a ×N badge; dates never wrap", () => {
  const { container } = view({
    headline: "Two postings, one duplicated [2].",
    stats: [],
    sections: [
      {
        kind: "table",
        title: "Postings",
        columns: ["Title", "Posted"],
        rows: [
          ["Architect", "2026-03-04"],
          ["Architect", "2026-03-04"],
          ["Engineer", "14 May 2026"],
        ],
      },
    ],
  });
  const rows = container.querySelectorAll("tbody tr");
  expect(rows.length).toBe(2);
  const dup = rows[0]?.querySelector(".sa-dup");
  expect(dup?.textContent).toBe("×2");
  expect(dup?.getAttribute("aria-label")).toBe("2 identical rows");
  // both rows kept one date cell each after the merge, each with the no-wrap class
  expect(container.querySelectorAll(".sa-nowrap").length).toBe(2);
});

test("callouts carry their tone as a class", () => {
  const { container } = view(fixture);
  expect(container.querySelector(".sa-callout.warn")).not.toBeNull();
  for (const tone of ["info", "good"] as const) {
    const { container: c } = view({
      headline: "h",
      stats: [],
      sections: [{ kind: "callout", text: "t", tone }],
    });
    expect(c.querySelector(`.sa-callout.${tone}`)).not.toBeNull();
  }
});

test("a misshapen row is dropped and an empty table still opens", () => {
  const { container } = view({
    headline: "h",
    stats: [],
    sections: [
      {
        kind: "table",
        title: "t",
        columns: ["A", "B"],
        rows: [["1", "2"], ["3"], ["4", "5", "6"]],
      },
    ],
  });
  const rows = container.querySelectorAll("tbody tr");
  expect(rows.length).toBe(1);
  expect(rows[0]?.textContent).toBe("12");
});

test("a finish()-shaped answer never shows a raw row marker, passage citations included", () => {
  // what answers.finish emits after numbering: cites are [n], leftovers already stripped
  const { container } = view({
    headline: "The roadmap sits with the platform owner [1]; see [2].",
    stats: [{ label: "documents", value: "2" }],
    sections: [
      {
        kind: "table",
        title: "Ownership",
        columns: ["Who", "Evidence"],
        rows: [["Platform owner", "[1]"], ["Program board", "[2]"]],
      },
    ],
  });
  expect(container.innerHTML).not.toContain("[[");
  expect(container.innerHTML).not.toContain("passage:");
  const cited = new Set(
    Array.from(container.querySelectorAll(".cite[data-cite]")).map((c) => c.getAttribute("data-cite")),
  );
  expect([...cited].sort()).toEqual(["1", "2"]);
});

test("citedText tokenizes citations and bold and nothing else", () => {
  const { container } = render(<>{citedText("a [12] b **bold** c <i>not html</i>", chip)}</>);
  expect(container.querySelector(".cite")?.textContent).toBe("12");
  expect(container.querySelector("strong")?.textContent).toBe("bold");
  expect(container.querySelector("i")).toBeNull();
  expect(container.textContent).toBe("a 12 b bold c <i>not html</i>");
});
