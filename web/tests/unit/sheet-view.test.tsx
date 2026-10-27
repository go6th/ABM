// @vitest-environment jsdom
// A delivered file's table on screen: the file's headers in the file's order, a link column
// showing the file's own text ("Open profile") and opening the address, the source code opening
// its source, and the caption naming the file's sheet and its row order.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { Sheet } from "@/lib/gen/api.gen";
import { SheetView } from "@/components/accounts/SheetView";

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
          profile_url: { href: "/v1/accounts/synthetic.example/archive?url=https%3A%2F%2Fwww.linkedin.com%2Fin%2Fsynthetic-director", archived_at: "2026-10-27T09:00:00+00:00" },
          link: { href: "https://evil.example/not-ours", archived_at: "2026-10-27T09:00:00+00:00" },
        },
      },
    ],
  };
  render(<SheetView sheet={archived} domain="synthetic.example" base="/accounts/synthetic.example/people" moreHref={null} />);
  const table = screen.getByRole("table");
  const copies = within(table).getAllByRole("link", { name: "archived" });
  // the profile's copy opens the API's same-origin page; the source link's copy pointed off
  // the origin and is not shown
  expect(copies).toHaveLength(1);
  expect(copies[0]!.getAttribute("href")).toBe("/v1/accounts/synthetic.example/archive?url=https%3A%2F%2Fwww.linkedin.com%2Fin%2Fsynthetic-director");
  expect(copies[0]!.getAttribute("title")).toBe("Archived copy, captured 2026-10-27");
  expect(within(table).getByRole("link", { name: "Open profile" })).toBeTruthy();
});
