"use client";
// The operator console navigation in two groups: the platform, and the grant-based support tools.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Coins, Eye, Gauge, Receipt, ShieldAlert, SlidersHorizontal, Store } from "lucide-react";

export const GROUPS = [
  { label: "Platform", entries: [
    { href: "/operator", label: "Overview", icon: Gauge },
    { href: "/operator/tenants", label: "Tenants", icon: Store },
    { href: "/operator/configuration", label: "Flags and keys", icon: SlidersHorizontal },
  ] },
  { label: "Support tools", entries: [
    { href: "/operator/support", label: "Support view", icon: Eye },
    { href: "/operator/runs", label: "Runs", icon: Activity },
    { href: "/operator/spend", label: "Spend", icon: Coins },
    { href: "/operator/cost", label: "Unit costs", icon: Receipt },
    { href: "/operator/failures", label: "Failures", icon: ShieldAlert },
  ] },
] as const;

export function isCurrentOperator(pathname: string, href: string): boolean {
  return pathname === href || (href !== "/operator" && pathname.startsWith(`${href}/`));
}

export function OperatorNav() {
  const pathname = usePathname();
  return (
    <nav className="app-nav" aria-label="Operator console navigation">
      {GROUPS.map((group) => (
        <div key={group.label} className="stack-tight">
          <p className="nav-label">{group.label}</p>
          {group.entries.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} aria-current={isCurrentOperator(pathname, href) ? "page" : undefined}>
              <Icon size={18} strokeWidth={1.7} aria-hidden="true" />
              <span>{label}</span>
            </Link>
          ))}
        </div>
      ))}
    </nav>
  );
}
