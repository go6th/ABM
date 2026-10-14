"use client";
// The client workspace frame as designed: logo, workspace card, New chat, the nav (Ask,
// Accounts, New research, Schedules, Mail and drive), recent chats, then the signed-in person and
// Sign out (no "Accounts used" meter: owner decision 2026-10-14; Add account still enforces the
// cap). Below 760px the sidebar is an overlay menu: Escape or the
// scrim closes it, focus stays inside while open and returns to the Menu button, and the page
// behind is inert.
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, clearSession } from "@/lib/api";
import { Logo, NavIcon } from "@/ui/design";

export interface RecentChat { id: string; title: string }

export interface ShellNote { id: string; text: string; meta: string; href: string; warn: boolean }

export interface ShellUser {
  clientName: string;
  userName: string | null;
  email: string | null;
  mailDriveAllowed: boolean;
  /** False when the AI model is not set up (``/v1/me`` ``model_ready``). */
  modelReady?: boolean;
  /** A client admin: may give 6th support read-only access. */
  isAdmin?: boolean;
}

export const NEW_CHAT_EVENT = "ask:new-chat";

export function isCurrent(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/research/new") return pathname === href || /^\/accounts\/[^/]+\/files/.test(pathname);
  if (href === "/accounts") return pathname === "/accounts" || (pathname.startsWith("/accounts/") && !/^\/accounts\/[^/]+\/files/.test(pathname));
  return pathname === href || pathname.startsWith(`${href}/`);
}

function initial(name: string | null, email: string | null): string {
  return ((name ?? email ?? "?").trim()[0] ?? "?").toUpperCase();
}

export function WorkspaceShell({ user, recent, notes = [], children }: { user: ShellUser; recent: RecentChat[]; notes?: ShellNote[]; children: React.ReactNode }) {
  const pathname = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const side = useRef<HTMLElement>(null);
  const thread = params.get("thread");

  const nav = [
    { href: "/", label: "Ask", icon: "ask" },
    { href: "/accounts", label: "Accounts", icon: "accounts" },
    { href: "/research/new", label: "New research", icon: "research" },
    { href: "/schedules", label: "Schedules", icon: "schedules" },
    ...(user.mailDriveAllowed ? [{ href: "/mail-and-drive", label: "Mail and drive", icon: "mail" }] : []),
    ...(user.isAdmin ? [{ href: "/support-access", label: "Support access", icon: "support" }] : []),
  ];

  const close = useCallback(() => {
    setOpen(false);
    menuButton.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const first = side.current?.querySelector<HTMLElement>("a, button");
    first?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close(); return; }
      if (event.key !== "Tab" || !side.current) return;
      const focusable = Array.from(side.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled])"));
      if (!focusable.length) return;
      const firstEl = focusable[0]!;
      const lastEl = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === firstEl) { event.preventDefault(); lastEl.focus(); }
      else if (!event.shiftKey && document.activeElement === lastEl) { event.preventDefault(); firstEl.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  const signOut = async () => {
    if (busy) return;
    setBusy(true);
    try { await fetch("/api/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" } }); } catch { /* cleared below */ }
    clearSession();
    router.push("/login");
    router.refresh();
  };

  const newChat = () => {
    setOpen(false);
    window.dispatchEvent(new Event(NEW_CHAT_EVENT));
    router.push("/");
  };

  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="topbar" inert={open || undefined}>
        <Link href="/" aria-label="Account Intelligence home"><Logo /></Link>
        <button ref={menuButton} type="button" className="btn sm" aria-expanded={open} aria-controls="sidebar" onClick={() => setOpen(true)}>Menu</button>
      </div>
      <button type="button" className="scrim" data-open={open} aria-label="Close menu" tabIndex={-1} onClick={close} />
      <aside ref={side} className="side" id="sidebar" data-open={open} aria-label="Workspace">
        <Link href="/" prefetch={false} onClick={() => setOpen(false)}><Logo /></Link>
        <div className="card wscard"><div className="k">Workspace</div><div className="v" title={user.clientName}>{user.clientName}</div></div>
        <button type="button" className="btn primary block" onClick={newChat}>New chat</button>
        <nav className="nav" aria-label="Main">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              prefetch={false}
              aria-current={isCurrent(pathname, item.href) && !(item.href === "/" && thread) ? "page" : undefined}
              onClick={() => setOpen(false)}
            >
              <NavIcon name={item.icon} />{item.label}
            </Link>
          ))}
        </nav>
        {notes.length ? (
          <section className="notes" aria-label="Notifications">
            <div className="lbl">Updates</div>
            {notes.map((n) => (
              <Link
                key={n.id}
                href={n.href}
                prefetch={false}
                className={n.warn ? "note warn" : "note"}
                onClick={() => {
                  setOpen(false);
                  void api("POST", `/v1/notifications/${encodeURIComponent(n.id)}/read`).then(() => router.refresh()).catch(() => undefined);
                }}
              >
                {n.text}<span className="meta">{n.meta}</span>
              </Link>
            ))}
          </section>
        ) : null}
        {recent.length ? (
          <nav className="rec" aria-label="Recent chats">
            <div className="lbl">Recent</div>
            {recent.map((t) => (
              <Link key={t.id} href={`/?thread=${encodeURIComponent(t.id)}`} prefetch={false} aria-current={thread === t.id ? "page" : undefined} onClick={() => setOpen(false)}>
                {t.title || "Untitled chat"}
              </Link>
            ))}
          </nav>
        ) : null}
        <div className="side-foot">
          <div className="me">
            <div className="avatar" aria-hidden="true">{initial(user.userName, user.email)}</div>
            <div className="who">
              {user.userName ? <div className="nm">{user.userName}</div> : null}
              {user.email ? <div className="em" title={user.email}>{user.email}</div> : null}
              <button type="button" className="out" onClick={() => void signOut()} disabled={busy}>Sign out</button>
            </div>
          </div>
        </div>
      </aside>
      <main className="main" id="main" tabIndex={-1} inert={open || undefined}>
        {user.modelReady === false ? (
          <p className="model-missing" role="status">Answers need the AI model to be set up by your administrator.</p>
        ) : null}
        {children}
      </main>
    </div>
  );
}
