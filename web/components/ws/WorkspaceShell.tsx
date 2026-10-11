"use client";
// The client workspace frame: product and client name, the account cap, and a rail of at most
// seven destinations. The client name comes from the caller's own profile; nothing here names
// any other client. Below 760px the rail becomes a bottom bar.
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { Bell, Building2, Home, Activity, MessageSquare, Settings, LogOut } from "lucide-react";
import { clearSession } from "@/lib/api";
import { m } from "@/lib/messages";
import { wsRoot } from "./EvidenceDrawer";
import { SETTINGS_PATHS } from "./SettingsNav";

const NAV = [
  { href: "/", label: m.nav.home, icon: Home },
  { href: "/accounts", label: m.nav.accounts, icon: Building2 },
  { href: "/chat", label: m.nav.conversations, icon: MessageSquare },
  { href: "/alerts", label: m.nav.alerts, icon: Bell },
  { href: "/runs", label: m.nav.runs, icon: Activity },
] as const;

export function isCurrent(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function CapMeter({ used, limit }: { used: number; limit: number }) {
  const fraction = limit > 0 ? Math.min(used / limit, 1) : 0;
  const full = limit > 0 && used >= limit;
  return (
    <span className="ws-cap ws-small" data-full={full} title={full ? m.cap.full : undefined}>
      <svg viewBox="0 0 56 4" aria-hidden="true" focusable="false">
        <rect className="track" x="0" y="0" width="56" height="4" rx="2" />
        <rect className="fill" x="0" y="0" width={Math.max(fraction * 56, used > 0 ? 4 : 0)} height="4" rx="2" />
      </svg>
      <span className="text-long">{m.cap.used(used, limit)}</span>
      <span className="ws-sr">{full ? m.cap.full : ""}</span>
    </span>
  );
}

function initials(name: string | null, email: string | null): string {
  const source = (name ?? email ?? "").trim();
  if (!source) return "?";
  const parts = source.split(/[\s@.]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

function UserMenu({ name, email }: { name: string | null; email: string | null }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const signOut = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await fetch("/api/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" } });
    } catch {
      // the client state is cleared either way
    }
    clearSession();
    router.push("/login");
    router.refresh();
  };
  return (
    <Popover.Root>
      <Popover.Trigger className="ws-avatar" aria-label={name ?? email ?? m.nav.settings}>{initials(name, email)}</Popover.Trigger>
      <Popover.Portal container={wsRoot()}>
        <Popover.Content className="ws-menu" align="end" sideOffset={8}>
          <div className="who">
            {name ? <p className="ws-label">{name}</p> : null}
            {email ? <p className="ws-small ws-muted">{email}</p> : null}
          </div>
          <Link href="/settings/security"><Settings size={16} strokeWidth={1.6} aria-hidden="true" />{m.nav.settings}</Link>
          <button type="button" onClick={() => void signOut()} disabled={busy}>
            <LogOut size={16} strokeWidth={1.6} aria-hidden="true" />{m.nav.signOut}
          </button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

export function WorkspaceShell({
  clientName, userName, email, used, limit, children,
}: {
  clientName: string;
  userName: string | null;
  email: string | null;
  used: number;
  limit: number;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  return (
    <div className="ws" id="ws-root">
      <a className="ws-skip" href="#main">Skip to content</a>
      <header className="ws-top">
        <Link href="/" className="ws-product"><b>{m.product}</b><span>{m.productBy}</span></Link>
        <span className="ws-client">{clientName}</span>
        <div className="ws-top-end">
          {limit > 0 ? <CapMeter used={used} limit={limit} /> : null}
          <UserMenu name={userName} email={email} />
        </div>
      </header>
      <div className="ws-body">
        <nav className="ws-rail" aria-label={m.nav.label}>
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} aria-current={isCurrent(pathname, href) ? "page" : undefined}>
              <Icon size={18} strokeWidth={1.6} aria-hidden="true" />
              <span>{label}</span>
            </Link>
          ))}
          <Link className="end" href="/settings/security" aria-current={SETTINGS_PATHS.some((p) => isCurrent(pathname, p)) ? "page" : undefined}>
            <Settings size={18} strokeWidth={1.6} aria-hidden="true" />
            <span>{m.nav.settings}</span>
          </Link>
        </nav>
        <main className="ws-main" id="main">{children}</main>
      </div>
    </div>
  );
}
