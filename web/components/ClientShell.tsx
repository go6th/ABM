"use client";

import {
  Activity,
  Bell,
  BookOpen,
  Building2,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  LayoutDashboard,
  Menu,
  ScrollText,
  Settings,
  ShieldCheck,
  Users,
  Wallet,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useSyncExternalStore, type ReactNode } from "react";
import { SignOutButton } from "@/components/SignOutButton";
import { ThemeToggle } from "@/components/ThemeToggle";
import type { Me } from "@/lib/gen/api.gen";

interface ClientShellProps {
  me: Me;
  displayName: string;
  attribution: string;
  children: ReactNode;
}

interface NavigationItem {
  label: string;
  href: string;
  capability: string;
  icon: LucideIcon;
}

interface NavigationGroup {
  label: string;
  items: NavigationItem[];
}

const SIDEBAR_PREFERENCE = "abm-sidebar-collapsed";
const SIDEBAR_CHANGE_EVENT = "abm-sidebar-preference-change";
let sidebarFallback = false;

const NAVIGATION: NavigationGroup[] = [
  {
    label: "Workspace",
    items: [
      { label: "Overview", href: "/", capability: "brief:public:read", icon: LayoutDashboard },
      { label: "Accounts", href: "/accounts", capability: "brief:public:read", icon: Building2 },
      { label: "Research runs", href: "/runs", capability: "brief:public:read", icon: Activity },
      { label: "Ask research", href: "/chat", capability: "brief:public:read", icon: BookOpen },
      { label: "Signals & alerts", href: "/alerts", capability: "brief:public:read", icon: Bell },
      { label: "Alert preferences", href: "/settings/alerts", capability: "brief:public:read", icon: Bell },
    ],
  },
  {
    label: "Administration",
    items: [
      { label: "Team & access", href: "/settings/users", capability: "users:manage", icon: Users },
      { label: "Usage & plan", href: "/usage", capability: "usage:read", icon: Wallet },
      { label: "Audit trail", href: "/audit", capability: "audit:read", icon: ScrollText },
      { label: "Settings", href: "/settings", capability: "settings:manage", icon: Settings },
      { label: "Integrations", href: "/settings/connectors", capability: "settings:manage", icon: Building2 },
      { label: "CRM write-back", href: "/settings/crm", capability: "settings:manage", icon: ClipboardList },
      { label: "Support access", href: "/settings/support", capability: "settings:manage", icon: ShieldCheck },
      { label: "Security", href: "/settings/security", capability: "sessions:revoke:self", icon: ShieldCheck },
    ],
  },
];

const BREADCRUMB_LABELS: Record<string, string> = {
  accounts: "Accounts",
  alerts: "Signals & alerts",
  audit: "Audit trail",
  chat: "Ask research",
  history: "Research history",
  runs: "Research runs",
  settings: "Settings",
  security: "Security",
  users: "Team & access",
  usage: "Usage & plan",
};

function subscribeViewport(notify: () => void) {
  const media = window.matchMedia("(max-width: 900px)");
  media.addEventListener("change", notify);
  return () => media.removeEventListener("change", notify);
}

function mobileViewportSnapshot(): boolean {
  return window.matchMedia("(max-width: 900px)").matches;
}

function subscribeSidebarPreference(notify: () => void) {
  window.addEventListener(SIDEBAR_CHANGE_EVENT, notify);
  window.addEventListener("storage", notify);
  return () => {
    window.removeEventListener(SIDEBAR_CHANGE_EVENT, notify);
    window.removeEventListener("storage", notify);
  };
}

function sidebarPreferenceSnapshot(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_PREFERENCE) === "true";
  } catch {
    return sidebarFallback;
  }
}

function saveSidebarPreference(collapsed: boolean): void {
  sidebarFallback = collapsed;
  try {
    window.localStorage.setItem(SIDEBAR_PREFERENCE, String(collapsed));
  } catch {
    // The in-memory preference still applies when browser storage is unavailable.
  }
  window.dispatchEvent(new Event(SIDEBAR_CHANGE_EVENT));
}

function displaySegment(segment: string): string {
  try {
    return decodeURIComponent(segment).replaceAll("_", " ");
  } catch {
    return segment;
  }
}

function Breadcrumbs({ pathname, isAdmin }: { pathname: string; isAdmin: boolean }) {
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length === 0) {
    return <span className="client-current-page">{isAdmin ? "Admin overview" : "Overview"}</span>;
  }

  return (
    <nav aria-label="Breadcrumb" className="client-breadcrumbs">
      <ol>
        {parts.map((part, index) => {
          const href = `/${parts.slice(0, index + 1).join("/")}`;
          const label = BREADCRUMB_LABELS[part] ?? displaySegment(part);
          const current = index === parts.length - 1;
          return (
            <li key={`${href}-${index}`} aria-current={current ? "page" : undefined}>
              {current ? <span>{label}</span> : <Link href={href}>{label}</Link>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function ClientShell({ me, displayName, attribution, children }: ClientShellProps) {
  const pathname = usePathname();
  const isAdmin = me.role === "admin";
  const [mobileOpen, setMobileOpen] = useState(false);
  const collapsed = useSyncExternalStore(
    subscribeSidebarPreference,
    sidebarPreferenceSnapshot,
    () => false,
  );
  const mobileViewport = useSyncExternalStore(
    subscribeViewport,
    mobileViewportSnapshot,
    () => false,
  );

  const groups = NAVIGATION.map((group) => ({
    ...group,
    items: group.items.filter((item) => me.capabilities.includes(item.capability)),
  })).filter((group) => group.items.length > 0);

  return (
    <div
      className="shell client-shell"
      data-collapsed={collapsed ? "true" : "false"}
      data-drawer={mobileOpen ? "open" : "closed"}
    >
      <a className="skip" href="#main">Skip to content</a>

      {mobileOpen ? (
        <button
          className="client-drawer-scrim"
          type="button"
          aria-label="Close workspace navigation"
          onClick={() => setMobileOpen(false)}
        />
      ) : null}

      <aside
        className="sidebar client-sidebar"
        aria-label="Workspace"
        aria-hidden={mobileViewport && !mobileOpen}
        inert={mobileViewport && !mobileOpen}
      >
        <div className="sidebar-head">
          <Link className="wordmark" href="/" aria-label={`${displayName} workspace home`}>
            <span className="mark" aria-hidden="true">6</span>
            <span className="hide-collapsed">{displayName}</span>
            <span className="sub hide-collapsed">Workspace</span>
          </Link>
          <button
            className="btn icon ghost desktop-only"
            type="button"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!collapsed}
            onClick={() => saveSidebarPreference(!collapsed)}
          >
            {collapsed ? <ChevronRight aria-hidden="true" /> : <ChevronLeft aria-hidden="true" />}
          </button>
          <button
            className="btn icon ghost mobile-only"
            type="button"
            aria-label="Close workspace navigation"
            onClick={() => setMobileOpen(false)}
          >
            <X aria-hidden="true" />
          </button>
        </div>

        <nav className="sidebar-scroll" id="main-navigation" aria-label="Main navigation">
          {groups.map((group) => (
            <div className="nav-group" key={group.label}>
              <p className="nav-title">{group.label}</p>
              {group.items.map(({ label, href, icon: Icon }) => {
                const itemLabel = isAdmin && href === "/" ? "Admin overview" : label;
                const active = pathname === href || (href !== "/" && pathname.startsWith(`${href}/`));
                return (
                  <Link
                    className="nav-link"
                    key={href}
                    href={href}
                    title={collapsed ? itemLabel : undefined}
                    aria-label={itemLabel}
                    aria-current={active ? "page" : undefined}
                    onClick={() => setMobileOpen(false)}
                  >
                    <Icon aria-hidden="true" />
                    <span className="nav-text">{itemLabel}</span>
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="user-chip" aria-label={`Signed in as ${me.display_name ?? me.email ?? me.role}`}>
            <span className="avatar" aria-hidden="true">{me.role.slice(0, 1).toUpperCase()}</span>
            <span className="who hide-collapsed">
              <strong>{me.display_name ?? me.email ?? "Workspace member"}</strong>
              <span>{me.role}</span>
            </span>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar client-topbar">
          <button
            className="btn icon ghost mobile-only"
            type="button"
            aria-label="Open workspace navigation"
            aria-controls="main-navigation"
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen(true)}
          >
            <Menu aria-hidden="true" />
          </button>
          <Breadcrumbs pathname={pathname} isAdmin={isAdmin} />
          <span className="spacer" />
          <div className="topbar-actions">
            {isAdmin && (
              <Link className="topbar-action" href="/settings/users" title="Team & access">
                <Users aria-hidden="true" />
                <span>Team</span>
              </Link>
            )}
            <Link className="topbar-action" href="/settings" title="Settings">
              <Settings aria-hidden="true" />
              <span>Settings</span>
            </Link>
            <span className="client-organization" title="Active organisation">
              <Building2 aria-hidden="true" />
              <span>{me.tenant_name || displayName}</span>
            </span>
            <ThemeToggle />
            <SignOutButton />
          </div>
        </header>

        <main id="main" className="column wide client-content">{children}</main>
        <footer className="client-footer">Prepared by {attribution}</footer>
      </div>
    </div>
  );
}
