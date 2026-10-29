// @vitest-environment jsdom
// One sheet of the delivered workbook as the file prints it: a cell's fill token becomes a class
// (the colour value lives in the theme and the stylesheet, never inline), a long cell wraps, a
// link cell opens its address with the file's text, and an unknown fill token styles nothing.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { SheetTab } from "@/lib/gen/api.gen";
import { TabView } from "@/components/accounts/TabView";
import { fillClass, tdClass } from "@/components/accounts/cell-fill";

afterEach(() => cleanup());

const tab: SheetTab = {
  account_name: "Synthetic Ltd", file: "Tech stack.xlsx", key: "technology", tab: "platform-ranking", tab_label: "Platform Ranking",
  title: "Tech stack and teams", tabs: [{ key: "platform-ranking", label: "Platform Ranking" }, { key: "stack", label: "Stack detections", main: true }],
  blocks: [
    { kind: "text", text: "Synthetic Ltd: platforms seen in job postings, ranked A, B, C" },
    {
      kind: "table",
      header: ["Rank", "Platform", "Postings naming it", "Link"],
      rows: [
        [{ text: "A", fill: "rank_a" }, { text: "Salesforce" }, { text: "12" }, { text: "Open posting", href: "https://jobs.example/1" }],
        [{ text: "B", fill: "rank_b" }, { text: "SAP", fill: "mystery" }, { text: "4" }, { text: "" }],
        [{ text: "", fill: null }, { text: "x".repeat(100) }, { text: "1" }, { text: "" }],
      ],
    },
  ],
};

test("fill tokens become classes, long cells wrap, links keep the file's text", () => {
  render(<TabView tab={tab} />);
  const table = screen.getByRole("table");
  expect(within(table).getByText("A").closest("td")?.className).toBe("fill-rank-a");
  expect(within(table).getByText("B").closest("td")?.className).toBe("fill-rank-b");
  expect(within(table).getByText("SAP").closest("td")?.className ?? "").not.toMatch(/fill-/);
  expect(within(table).getByText("x".repeat(100)).closest("td")?.className).toBe("wrap");
  expect(within(table).getByRole("link", { name: "Open posting" }).getAttribute("href")).toBe("https://jobs.example/1");
  expect(screen.getByText(/platforms seen in job postings/)).toBeTruthy();
});

test("the fill helpers map only the known tokens", () => {
  expect(fillClass("priority_a")).toBe("fill-priority-a");
  expect(fillClass("new_row")).toBe("fill-new-row");
  expect(fillClass("mystery")).toBeNull();
  expect(fillClass(null)).toBeNull();
  expect(tdClass("wrap", null, false, "fill-rank-c")).toBe("wrap fill-rank-c");
  expect(tdClass(null, false)).toBeUndefined();
});
