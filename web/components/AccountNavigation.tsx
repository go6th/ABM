"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const SECTIONS = [
  { label: "Summary", table: null },
  { label: "Facts & evidence", table: "facts" },
  { label: "People", table: "people" },
  { label: "Job postings", table: "jobs" },
  { label: "Technology stack", table: "tech" },
  { label: "Coverage tracker", table: "coverage" },
  { label: "Signals & history", table: "history" },
  { label: "Exports", table: "exports" },
] as const;

export function AccountNavigation({ domain }: { domain: string }) {
  const pathname = usePathname();
  const base = `/accounts/${encodeURIComponent(domain)}`;
  const currentTable = pathname.startsWith(`${base}/tables/`)
    ? pathname.slice(`${base}/tables/`.length).split("/")[0]
    : null;
  const onHistory = pathname === `${base}/history`;

  return (
    <nav className="tabs account-tabs" aria-label="Account sections">
      {SECTIONS.map((section) => {
        const href = section.table === null
          ? base
          : section.table === "history"
            ? `${base}/history`
            : section.table === "exports"
              ? `${base}#exports`
              : `${base}/tables/${section.table}`;
        const current = section.table === null
          ? pathname === base
          : section.table === "history"
            ? onHistory
            : section.table === "exports"
              ? false
              : currentTable === section.table;
        return (
          <Link
            className="tab"
            key={section.label}
            href={href}
            aria-current={current ? "page" : undefined}
          >
            {section.label}
          </Link>
        );
      })}
    </nav>
  );
}
