"use client";
// Page-level tabs (redesign 2026-11-07): links, so each tab is its own address; the open one is
// marked aria-current="page". Settings and the account page both use them. A tab counts as open
// on its own address and, when ``prefix`` is set, on every address under it.
import Link from "next/link";
import { usePathname } from "next/navigation";

export interface PageTab { href: string; label: string; count?: number | null; prefix?: boolean }

export function PageTabs({ tabs, label }: { tabs: PageTab[]; label: string }) {
  const pathname = usePathname();
  return (
    <nav className="ptabs" aria-label={label}>
      {tabs.map((t) => {
        const on = pathname === t.href || (t.prefix === true && pathname.startsWith(`${t.href}/`));
        return (
          <Link key={t.href} href={t.href} aria-current={on ? "page" : undefined} prefetch={false}>
            {t.label}
            {typeof t.count === "number" ? <span className="n">{t.count.toLocaleString()}</span> : null}
          </Link>
        );
      })}
    </nav>
  );
}
