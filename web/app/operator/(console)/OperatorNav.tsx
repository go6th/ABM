"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Bell,
  ClipboardList,
  Coins,
  FileClock,
  Gauge,
  Megaphone,
  Eye,
  SlidersHorizontal,
  ShieldAlert,
  Store,
} from "lucide-react";

const entries = [
  { href: "/operator", label: "Overview", icon: Gauge, connected: true },
  { href: "/operator/tenants", label: "Tenants", icon: Store, connected: true },
  { href: "/operator/configuration", label: "Flags & keys", icon: SlidersHorizontal, connected: true },
  { href: "/operator/support", label: "Support view", icon: Eye, connected: true },
  { href: "/operator/runs", label: "Runs", icon: Activity, connected: true },
  { href: "/operator/spend", label: "Spend", icon: Coins, connected: true },
  { href: "/operator/review", label: "Review queue", icon: ClipboardList, connected: false },
  { href: "/operator/cost", label: "Unit costs", icon: Coins, connected: true },
  { href: "/operator/failures", label: "Failures", icon: ShieldAlert, connected: true },
  { href: "/operator/monitors", label: "Monitors", icon: Bell, connected: false },
  { href: "/operator/audit", label: "Audit explorer", icon: FileClock, connected: false },
  { href: "/operator/announcements", label: "Status & notices", icon: Megaphone, connected: false },
];

export function OperatorNav() {
  const pathname = usePathname();
  const production = process.env.NODE_ENV === "production";
  return (
    <nav className="operator-nav" aria-label="Operator console navigation">
      {entries.filter((entry) => !production || entry.connected).map((entry) => {
        const Icon = entry.icon;
        const current = pathname === entry.href || (entry.href !== "/operator" && pathname.startsWith(`${entry.href}/`));
        return (
          <Link key={entry.href} className="operator-nav-link" href={entry.href} aria-current={current ? "page" : undefined}>
            <Icon aria-hidden="true" />
            <span>{entry.label}</span>
            {!entry.connected ? <span className="nav-dev-tag">preview</span> : null}
          </Link>
        );
      })}
    </nav>
  );
}
