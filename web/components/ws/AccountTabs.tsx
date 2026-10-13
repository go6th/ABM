"use client";
// The account tabs: Overview, People, Jobs, Technology, News, Sources, Files.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { m } from "@/lib/messages";

export const TABS = [
  { key: "", label: m.account.tabs.overview },
  { key: "people", label: m.account.tabs.people },
  { key: "jobs", label: m.account.tabs.jobs },
  { key: "technology", label: m.account.tabs.tech },
  { key: "news", label: m.account.tabs.news },
  { key: "sources", label: m.account.tabs.sources },
  { key: "files", label: m.account.tabs.files },
] as const;

export function AccountTabs({ domain }: { domain: string }) {
  const pathname = usePathname();
  const base = `/accounts/${encodeURIComponent(domain)}`;
  return (
    <nav className="tabs" aria-label="Account sections">
      {TABS.map((t) => {
        const href = t.key ? `${base}/${t.key}` : base;
        return <Link key={t.key} href={href} aria-current={pathname === href ? "page" : undefined}>{t.label}</Link>;
      })}
    </nav>
  );
}
