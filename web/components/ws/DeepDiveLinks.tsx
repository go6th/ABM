"use client";
// The single row of Deep dive links under an account's title (at most seven).
import Link from "next/link";
import { usePathname } from "next/navigation";
import { m } from "@/lib/messages";

export const DIVES = [
  { key: "people", href: "tables/people", label: m.workspace.dives.people },
  { key: "jobs", href: "tables/jobs", label: m.workspace.dives.jobs },
  { key: "tech", href: "tables/tech", label: m.workspace.dives.tech },
  { key: "sources", href: "tables/facts", label: m.workspace.dives.sources },
  { key: "coverage", href: "tables/coverage", label: m.workspace.dives.coverage },
  { key: "signals", href: "history", label: m.workspace.dives.signals },
] as const;

export function DeepDiveLinks({ domain }: { domain: string }) {
  const pathname = usePathname();
  const base = `/accounts/${encodeURIComponent(domain)}`;
  return (
    <nav className="tabs" aria-label={m.workspace.divesLabel}>
      <Link href={base} aria-current={pathname === base ? "page" : undefined}>{m.workspace.overview}</Link>
      {DIVES.map((d) => {
        const href = `${base}/${d.href}`;
        return <Link key={d.key} href={href} aria-current={pathname === href ? "page" : undefined}>{d.label}</Link>;
      })}
    </nav>
  );
}
