"use client";
// The client workspace frame: a translucent sidebar with the product, the client name, at most
// seven destinations, the account cap and the theme toggle; a sticky toolbar with the page name
// and the account menu. The client name comes from the caller's own profile; nothing here names
// any other client. Below 760px the sidebar becomes a bottom tab bar.
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { Bell, Building2, Home, Activity, FolderDown, MessageSquare, Settings, LogOut } from "lucide-react";
import { clearSession } from "@/lib/api";
import { m } from "@/lib/messages";
import { ThemeToggle } from "@/ui/ThemeToggle";
import type { ThemeChoice } from "@/ui/theme";
import { SETTINGS_PATHS } from "./SettingsNav";

const NAV = [
  { href: "/", label: m.nav.home, icon: Home },
  { href: "/accounts", label: m.nav.accounts, icon: Building2 },
  { href: "/chat", label: m.nav.conversations, icon: MessageSquare },
  { href: "/alerts", label: m.nav.alerts, icon: Bell },
  { href: "/runs", label: m.nav.runs, icon: Activity },
  { href: "/downloads", label: m.nav.downloads, icon: FolderDown },
] as const;

export function isCurrent(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function CapMeter({ used, limit }: { used: number; limit: number }) {
  const fraction = limit > 0 ? Math.min(used / limit, 1) : 0;
  const full = limit > 0 && used >= limit;
  const warn = !full && fraction >= 0.8;
  return (
    <span className="cap" data-full={full} title={full ? m.cap.full : undefined}>
      <svg className="meter" viewBox="0 0 56 6" preserveAspectRatio="none" data-tone={full ? "full" : warn ? "warn" : undefined} aria-hidden="true" focusable="false">
        <rect className="track" x="0" y="0" width="56" height="6" rx="3" />
        <rect className="fill" x="0" y="0" width={Math.max(fraction * 56, used > 0 ? 4 : 0)} height="6" rx="3" />
      </svg>
      <span className="text-long">{m.cap.used(used, limit)}</span>
      <span className="sr-only">{full ? m.cap.full : ""}</span>
    </span>
  );
}

function initials(name: string | null, email: string | null): string {
  const source = (name ?? email ?? "").trim();
  if (!source) return "?";
  const parts = source.split(/[\s@.]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

function UserMenu({ name, email, theme }: { name: string | null; email: string | null; theme: ThemeChoice }) {
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
      <Popover.Trigger className="avatar" aria-label={name ?? email ?? m.nav.settings}>{initials(name, email)}</Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="menu" align="end" sideOffset={8}>
          <div className="who">
            {name ? <p className="strong">{name}</p> : null}
            {email ? <p className="t-footnote ink-2">{email}</p> : null}
          </div>
          <div className="menu-theme"><span className="t-footnote ink-2">{m.theme.label}</span><ThemeToggle initial={theme} /></div>
          <Link href="/settings/security"><Settings size={16} strokeWidth={1.7} aria-hidden="true" />{m.nav.settings}</Link>
          <button type="button" onClick={() => void signOut()} disabled={busy}>
            <LogOut size={16} strokeWidth={1.7} aria-hidden="true" />{m.nav.signOut}
          </button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** The toolbar gains a hairline once the page scrolls under it. */
function useScrolled() {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const bar = ref.current;
    if (!bar) return;
    const update = () => { bar.dataset.scrolled = String(window.scrollY > 4); };
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);
  return ref;
}

function crumb(pathname: string): string {
  if (SETTINGS_PATHS.some((p) => isCurrent(pathname, p))) return m.nav.settings;
  return NAV.find((n) => n.href !== "/" && isCurrent(pathname, n.href))?.label ?? m.nav.home;
}

export function WorkspaceShell({
  clientName, userName, email, used, limit, theme, children,
}: {
  clientName: string;
  userName: string | null;
  email: string | null;
  used: number;
  limit: number;
  theme: ThemeChoice;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const toolbar = useScrolled();
  const inSettings = SETTINGS_PATHS.some((p) => isCurrent(pathname, p));
  return (
    <div className="app" data-plane="client">
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className="app-sidebar">
        <Link href="/" className="brand">
          <span className="brand-mark" aria-hidden="true">6</span>
          <span className="brand-name"><b>{m.product}</b><span>{m.productBy}</span></span>
        </Link>
        <p className="client-name" title={clientName}>{clientName}</p>
        <nav className="app-nav" aria-label={m.nav.label}>
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} className={href === "/downloads" || href === "/runs" ? "wide-only" : undefined} aria-current={isCurrent(pathname, href) && !inSettings ? "page" : undefined}>
              <Icon size={18} strokeWidth={1.7} aria-hidden="true" />
              <span>{label}</span>
            </Link>
          ))}
          <Link className="end" href="/settings/security" aria-current={inSettings ? "page" : undefined}>
            <Settings size={18} strokeWidth={1.7} aria-hidden="true" />
            <span>{m.nav.settings}</span>
          </Link>
        </nav>
        <div className="sidebar-foot">
          <div className="row"><span className="t-footnote ink-2">{m.theme.label}</span><ThemeToggle initial={theme} /></div>
        </div>
      </aside>
      <div className="app-column">
        <header className="app-toolbar" ref={toolbar}>
          <Link href="/" className="app-mobile-brand" aria-label={m.product}>
            <span className="brand-mark" aria-hidden="true">6</span>
            <span className="truncate">{clientName}</span>
          </Link>
          <span className="crumb">{crumb(pathname)}</span>
          <div className="end">
            {limit > 0 ? <CapMeter used={used} limit={limit} /> : null}
            <UserMenu name={userName} email={email} theme={theme} />
          </div>
        </header>
        <main className="app-main" id="main" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
}
