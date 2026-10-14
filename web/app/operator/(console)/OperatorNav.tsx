"use client";
// The admin console navigation as designed.
import Link from "next/link";
import { usePathname } from "next/navigation";

export const ENTRIES = [
  { href: "/operator", label: "Overview" },
  { href: "/operator/clients", label: "Clients" },
  { href: "/operator/users", label: "Users" },
  { href: "/operator/library", label: "6th Library" },
  { href: "/operator/runs", label: "Runs and spend" },
  { href: "/operator/defaults", label: "Platform defaults" },
] as const;

export function isCurrentOperator(pathname: string, href: string): boolean {
  return pathname === href || (href !== "/operator" && pathname.startsWith(`${href}/`));
}

export function OperatorNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="an" aria-label="Admin">
      {ENTRIES.map(({ href, label }) => (
        <Link key={href} href={href} onClick={onNavigate} aria-current={isCurrentOperator(pathname, href) ? "page" : undefined}>{label}</Link>
      ))}
    </nav>
  );
}
