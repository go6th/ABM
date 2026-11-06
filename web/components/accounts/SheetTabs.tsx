// The delivered workbook's sheets as tabs, in the file's order (People: Summary and People; Job
// postings: Summary, Jobs, Counts and Long text; Tech stack: Platform Ranking, Stack detections
// and Platform teams). The main sheet is the sortable table; every other tab shows its sheet as
// the file prints it. Tabs are links, so each one is its own address (Tab moves between them and
// Enter opens one, as for any link); the open one is marked aria-current="page".
import Link from "next/link";
import type { SheetTabRef } from "@/lib/gen/api.gen";

/** A tab's address: the main sheet is the table's own page, every other sheet is ``?tab=<key>``.
 * ``keep``: parameters every tab carries (the match an Ask citation opened), so going back to the
 * main sheet keeps the matched rows. */
export function tabHref(base: string, tab: SheetTabRef, keep: Record<string, string> = {}): string {
  const q = new URLSearchParams({ ...(tab.main ? {} : { tab: tab.key }), ...keep }).toString();
  return q ? `${base}?${q}` : base;
}

export function SheetTabs({ tabs, base, current, keep }: { tabs: SheetTabRef[] | null | undefined; base: string; current: string | null; keep?: Record<string, string> }) {
  // an older response has no tabs; a tab without a key or label cannot be opened
  const list = (tabs ?? []).filter((t) => t && t.key && t.label);
  if (list.length < 2) return null;
  return (
    <nav className="sheet-tabs" aria-label="Sheets of the file">
      {list.map((t) => {
        const on = current === null ? Boolean(t.main) : current === t.key;
        return (
          <Link key={t.key} href={tabHref(base, t, keep)} aria-current={on ? "page" : undefined} prefetch={false} scroll={false}>
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
