// Table query specs as the API accepts them (VW3). The server is the authority: it validates
// every column and operator; this module only builds well-formed specs for the UI and mirrors the
// operator allow-list so the UI never offers an operator the column refuses.

export type Op =
  | "eq" | "ne" | "contains" | "starts_with" | "in" | "gte" | "lte" | "is_empty" | "not_empty";

export interface Column {
  key: string;
  header: string;
  kind: string;
  sortable: boolean;
  operators: Op[];
}

export interface Filter {
  column: string;
  op: Op;
  value?: string | number | string[] | null;
}

export interface Sort {
  column: string;
  direction: "asc" | "desc";
}

export interface QuerySpec {
  filters: Filter[];
  sort: Sort[];
  search?: string;
  page_size?: number;
}

export const LIMITS = { maxFilters: 8, maxSorts: 2, maxValue: 200, maxSearch: 200 } as const;

export function emptySpec(): QuerySpec {
  return { filters: [], sort: [] };
}

const NO_VALUE: ReadonlySet<Op> = new Set(["is_empty", "not_empty"]);

export function needsValue(op: Op): boolean {
  return !NO_VALUE.has(op);
}

export function addFilter(spec: QuerySpec, column: Column, op: Op, raw: string): QuerySpec {
  if (!column.operators.includes(op)) throw new Error("operator not allowed for this column");
  if (spec.filters.length >= LIMITS.maxFilters) throw new Error("too many filters");
  let value: Filter["value"] = null;
  if (needsValue(op)) {
    const text = raw.trim();
    if (!text || text.length > LIMITS.maxValue) throw new Error("a value is required");
    value = op === "in" ? text.split(",").map((v) => v.trim()).filter(Boolean) : text;
  }
  const filter: Filter = needsValue(op) ? { column: column.key, op, value } : { column: column.key, op };
  return { ...spec, filters: [...spec.filters, filter] };
}

export function removeFilter(spec: QuerySpec, index: number): QuerySpec {
  return { ...spec, filters: spec.filters.filter((_, i) => i !== index) };
}

export function toggleSort(spec: QuerySpec, column: Column): QuerySpec {
  if (!column.sortable) return spec;
  const current = spec.sort.find((s) => s.column === column.key);
  const others = spec.sort.filter((s) => s.column !== column.key);
  if (!current) return { ...spec, sort: [{ column: column.key, direction: "asc" as const }, ...others].slice(0, LIMITS.maxSorts) };
  if (current.direction === "asc") return { ...spec, sort: [{ column: column.key, direction: "desc" as const }, ...others] };
  return { ...spec, sort: others };
}

export function withSearch(spec: QuerySpec, text: string): QuerySpec {
  const search = text.trim().slice(0, LIMITS.maxSearch);
  const next: QuerySpec = { filters: spec.filters, sort: spec.sort };
  if (spec.page_size !== undefined) next.page_size = spec.page_size;
  if (search) next.search = search;
  return next;
}

export function tableUrl(domain: string, table: string, spec: QuerySpec, cursor?: string | null, viewId?: string | null): string {
  const params = new URLSearchParams();
  if (viewId) params.set("view_id", viewId);
  else if (spec.filters.length || spec.sort.length || spec.search || spec.page_size) params.set("spec", JSON.stringify(spec));
  if (cursor) params.set("cursor", cursor);
  const query = params.toString();
  return `/v1/accounts/${encodeURIComponent(domain)}/tables/${encodeURIComponent(table)}${query ? `?${query}` : ""}`;
}
