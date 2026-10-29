// The fill of a cell as the delivered file colours it: the API names a theme token (the hex
// value lives in the export theme), the screen maps the token to a class in components.css.
// Only the known tokens become classes, so a token the screen does not know styles nothing.
const FILLS = new Set(["priority_a", "priority_b", "priority_c", "rank_a", "rank_b", "rank_c", "new_row"]);

export function fillClass(token: string | null | undefined): string | null {
  return typeof token === "string" && FILLS.has(token) ? `fill-${token.replace(/_/g, "-")}` : null;
}

/** A td's class list from its parts (null and empty parts dropped), or undefined when empty. */
export function tdClass(...parts: Array<string | null | undefined | false>): string | undefined {
  const list = parts.filter((p): p is string => typeof p === "string" && p.length > 0);
  return list.length ? list.join(" ") : undefined;
}
