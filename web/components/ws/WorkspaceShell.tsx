"use client";
// The client workspace frame (redesign 2026-11-07): logo, workspace card, the nav (Accounts,
// Research, Settings), updates, then the signed-in person and Sign out. Ask and its recent chats
// left the sidebar on 2026-11-08 (owner: "remove that Ask from the left side"); the logo opens
// Accounts (no "Accounts used" meter: owner decision 2026-10-14; Add account still enforces the
// cap). Below 760px the sidebar is an overlay menu: Escape or the
// scrim closes it, focus stays inside while open and returns to the Menu button, and the page
// behind is inert.
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, clearSession } from "@/lib/api";
import type { ShellNote } from "@/lib/notifications";
import { setUploadOwner } from "@/lib/upload-store";
import { Logo, NavIcon } from "@/ui/design";
import { NavProgress } from "@/ui/NavProgress";
import { Toaster } from "@/ui/Toaster";

export type { ShellNote };

export interface ShellUser {
  /** The signed-in user's id: keeps this browser's paused uploads per person. */
  userId?: string;
  clientName: string;
  userName: string | null;
  email: string | null;
  /** False when the AI model is not set up (``/v1/me`` ``model_ready``). */
  modelReady?: boolean;
  /** A client admin: may give 6th support read-only access. */
  isAdmin?: boolean;
}

export const NEW_CHAT_EVENT = "ask:new-chat";

export function isCurrent(pathname: string, href: string): boolean {
  // starting, watching and reading a research all sit under Research
  if (href === "/research") return pathname === "/research" || pathname.startsWith("/research/") || pathname.startsWith("/runs/");
  // every account screen, its file analysis included, sits under Accounts
  if (href === "/accounts") return pathname === "/accounts" || pathname.startsWith("/accounts/");
  return pathname === href || pathname.startsWith(`${href}/`);
}

function initial(name: string | null, email: string | null): string {
  return ((name ?? email ?? "?").trim()[0] ?? "?").toUpperCase();
}

export function WorkspaceShell({ user, notes = [], children }: { user: ShellUser; notes?: ShellNote[]; children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // the "model not set up" notice can be dismissed; the shell persists across pages, so it stays
  // hidden until the next full load
  const [modelNoteHidden, setModelNoteHidden] = useState(false);
  const hideModelNote = () => setModelNoteHidden(true);
  const menuButton = useRef<HTMLButtonElement>(null);
  const side = useRef<HTMLElement>(null);

  const nav = [
    { href: "/accounts", label: "Accounts", icon: "accounts" },
    { href: "/research", label: "Research", icon: "research" },
    { href: "/settings", label: "Settings", icon: "settings" },
  ];

  useEffect(() => {
    setUploadOwner(user.userId ?? null);
  }, [user.userId]);

  // Escape and the scrim hand focus back to the Menu button, but only once the page behind
  // is no longer inert: the button sits in the inert top bar until the closed state renders.
  const refocus = useRef(false);
  const close = useCallback(() => {
    refocus.current = true;
    setOpen(false);
  }, []);

  useEffect(() => {
    if (open || !refocus.current) return;
    refocus.current = false;
    menuButton.current?.focus();
  }, [open]);

  // the overlay menu closes when the page changes and when the window grows past phone width
  // (where its scrim and Menu button are hidden, so it could not be closed and the page stayed inert)
  const [shownPath, setShownPath] = useState(pathname);
  if (shownPath !== pathname) { setShownPath(pathname); setOpen(false); }
  useEffect(() => {
    const wide = window.matchMedia("(min-width: 760px)");
    const onChange = () => { if (wide.matches) setOpen(false); };
    wide.addEventListener("change", onChange);
    return () => wide.removeEventListener("change", onChange);
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

  return (
    <div className="app ui-pastel">
      <NavProgress />
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="topbar" inert={open || undefined}>
        <Link href="/accounts" aria-label="Account Intelligence home"><Logo /></Link>
        <button ref={menuButton} type="button" className="btn sm" aria-expanded={open} aria-controls="sidebar" onClick={() => setOpen(true)}>Menu</button>
      </div>
      <button type="button" className="scrim" data-open={open} aria-label="Close menu" tabIndex={-1} onClick={close} />
      <aside ref={side} className="side" id="sidebar" data-open={open} aria-label="Workspace">
        <Link href="/accounts" prefetch={false} aria-label="Account Intelligence home" onClick={() => setOpen(false)}><Logo /></Link>
        <div className="card wscard"><div className="k">Workspace</div><div className="v" title={user.clientName}>{user.clientName}</div></div>
        <nav className="nav" aria-label="Main">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isCurrent(pathname, item.href) ? "page" : undefined}
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
        {user.modelReady === false && !modelNoteHidden ? (
          <p className="model-missing" role="status">
            Answers need the AI model to be set up by your administrator.
            <button type="button" className="x" onClick={hideModelNote}>Dismiss</button>
          </p>
        ) : null}
        {children}
      </main>
      <Toaster />
    </div>
  );
}
