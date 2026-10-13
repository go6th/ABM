"use client";
// The admin console navigation: Overview, Clients, Users, Runs, Settings.
import Link from "next/link";
import { usePathname } from "next/navigation";

export const ENTRIES = [
  { href: "/operator", label: "Overview" },
  { href: "/operator/clients", label: "Clients" },
  { href: "/operator/users", label: "Users" },
  { href: "/operator/runs", label: "Runs" },
  { href: "/operator/settings", label: "Settings" },
] as const;

export function isCurrentOperator(pathname: string, href: string): boolean {
  return pathname === href || (href !== "/operator" && pathname.startsWith(`${href}/`));
}

export function OperatorNav() {
  const pathname = usePathname();
  return (
    <nav className="nav" aria-label="Admin console">
      {ENTRIES.map(({ href, label }) => (
        <Link key={href} href={href} aria-current={isCurrentOperator(pathname, href) ? "page" : undefined}>{label}</Link>
      ))}
    </nav>
  );
}
