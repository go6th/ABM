"use client";
// The account's "More" tab (redesign 2026-11-07): the rest of the deep-dive tables in a small
// menu after the main tabs. It reads as the open tab when one of its pages is open, and names
// that page, so you always see where you are.
import Link from "next/link";
import { usePathname } from "next/navigation";

export function AccountMore({ items }: { items: Array<[string, string]> }) {
  const pathname = usePathname() ?? "";
  const open = items.find(([, href]) => pathname === href || pathname.startsWith(`${href}/`));
  return (
    <details className="dmenu more">
      <summary className="ptab-more" aria-current={open ? "page" : undefined}>{open ? open[0] : "More"}</summary>
      <ul className="menu-list">
        {items.map(([label, href]) => (
          <li key={href}><Link className="menu-item" href={href} prefetch={false} aria-current={open?.[1] === href ? "page" : undefined}>{label}</Link></li>
        ))}
      </ul>
    </details>
  );
}
