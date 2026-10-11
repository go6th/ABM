"use client";
// Settings navigation in small groups (at most seven items each). Items are shown by the
// capability the server grants; the server still refuses anything the caller cannot do.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { m } from "@/lib/messages";

const GROUPS = [
  { label: m.settings.you, items: [
    { href: "/settings/security", label: m.settings.security, cap: "sessions:revoke:self" },
    { href: "/settings/alerts", label: m.settings.alerts, cap: "brief:public:read" },
  ] },
  { label: m.settings.workspace, items: [
    { href: "/settings", label: m.settings.general, cap: "settings:manage" },
    { href: "/settings/users", label: m.settings.users, cap: "users:manage" },
    { href: "/usage", label: m.settings.usage, cap: "usage:read" },
    { href: "/audit", label: m.settings.audit, cap: "audit:read" },
  ] },
  { label: m.settings.connections, items: [
    { href: "/settings/connectors", label: m.settings.integrations, cap: "settings:manage" },
    { href: "/settings/crm", label: m.settings.crm, cap: "settings:manage" },
    { href: "/settings/support", label: m.settings.support, cap: "settings:manage" },
    { href: "/settings/model-key", label: m.settings.modelKey, cap: "settings:manage" },
  ] },
] as const;

export const SETTINGS_PATHS = ["/settings", "/usage", "/audit"];

export function SettingsNav({ capabilities, admin }: { capabilities: string[]; admin: boolean }) {
  const pathname = usePathname();
  const has = (cap: string) => admin || capabilities.includes(cap);
  return (
    <nav className="ws-settings-nav" aria-label={m.nav.settings}>
      {GROUPS.map((g) => {
        const items = g.items.filter((i) => has(i.cap));
        if (!items.length) return null;
        return (
          <div key={g.label}>
            <p className="ws-small ws-muted">{g.label}</p>
            {items.map((i) => (
              <Link key={i.href} href={i.href} aria-current={pathname === i.href ? "page" : undefined}>{i.label}</Link>
            ))}
          </div>
        );
      })}
    </nav>
  );
}
