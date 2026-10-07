// The client query builder (VW3): well-formed specs only, the server's operator allow-list
// mirrored, bounded values, URL encoding of every part.
import { describe, expect, it } from "vitest";
import { addFilter, emptySpec, removeFilter, tableUrl, toggleSort, withSearch, type Column } from "@/lib/query";

const name: Column = { key: "name", header: "Name", kind: "text_short", sortable: true, operators: ["eq", "contains", "is_empty"] };
const url: Column = { key: "profile_url", header: "Profile link", kind: "url", sortable: false, operators: ["contains"] };

describe("query builder", () => {
  it("adds filters only with allowed operators and bounded values", () => {
    const s = addFilter(emptySpec(), name, "contains", "  ann ");
    expect(s.filters).toEqual([{ column: "name", op: "contains", value: "ann" }]);
    expect(addFilter(s, name, "is_empty", "ignored").filters[1]).toEqual({ column: "name", op: "is_empty" });
    expect(() => addFilter(s, name, "gte", "x")).toThrow();
    expect(() => addFilter(s, name, "eq", "")).toThrow();
    expect(() => addFilter(s, name, "eq", "x".repeat(201))).toThrow();
    let full = emptySpec();
    for (let i = 0; i < 8; i++) full = addFilter(full, name, "eq", `v${i}`);
    expect(() => addFilter(full, name, "eq", "v9")).toThrow();
    expect(removeFilter(s, 0).filters).toEqual([]);
  });
  it("cycles sort asc, desc, off and refuses unsortable columns", () => {
    const a = toggleSort(emptySpec(), name);
    expect(a.sort).toEqual([{ column: "name", direction: "asc" }]);
    expect(toggleSort(a, name).sort).toEqual([{ column: "name", direction: "desc" }]);
    expect(toggleSort(toggleSort(a, name), name).sort).toEqual([]);
    expect(toggleSort(emptySpec(), url).sort).toEqual([]);
  });
  it("bounds search and encodes the URL", () => {
    expect(withSearch(emptySpec(), "x".repeat(300)).search).toHaveLength(200);
    expect(withSearch(emptySpec(), "   ").search).toBeUndefined();
    const u = tableUrl("acme.example", "people", addFilter(emptySpec(), name, "eq", "a&b=c"), "cur/sor");
    expect(u.startsWith("/v1/accounts/acme.example/tables/people?spec=")).toBe(true);
    expect(u).toContain("cursor=cur%2Fsor");
    expect(decodeURIComponent(u)).toContain('"value":"a&b=c"');
    expect(tableUrl("a.example", "jobs", emptySpec(), null, "view-1")).toBe("/v1/accounts/a.example/tables/jobs?view_id=view-1");
    expect(tableUrl("a/../b", "jobs", emptySpec())).toBe("/v1/accounts/a%2F..%2Fb/tables/jobs");
  });
});
