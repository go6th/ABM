"use client";
// The client workspace frame: a 220px sidebar with the workspace name, New chat, Ask, Accounts,
// New research and recent chats; the signed-in person at the foot. Nothing about caps or usage
// limits. Below 760px the sidebar folds behind a Menu button.
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { clearSession } from "@/lib/api";
import { m } from "@/lib/messages";
import { cellText } from "@/lib/render";

export interface RecentChat { id: string; title: string }

const NAV = [
  { href: "/", label: m.nav.ask },
  { href: "/accounts", label: m.nav.accounts },
  { href: "/research/new", label: m.nav.newResearch },
] as const;

export function isCurrent(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function WorkspaceShell({ clientName, userName, email, recent, children }: {
  clientName: string; userName: string | null; email: string | null; recent: RecentChat[]; children: React.ReactNode;
}) {
  const pathname = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const thread = params.get("thread");
  const signOut = async () => {
    if (busy) return;
    setBusy(true);
    try { await fetch("/api/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" } }); } catch { /* cleared below */ }
    clearSession();
    router.push("/login");
    router.refresh();
  };
  const sidebar = (
    <aside className="sidebar" data-open={open} id="sidebar">
      <Link href="/" className="brand">{m.product}<span className="small">{m.productBy}</span></Link>
      <p className="workspace">Workspace<b title={clientName}>{cellText(clientName)}</b></p>
      <Link className="btn primary" href="/" onClick={() => setOpen(false)}>{m.nav.newChat}</Link>
      <nav className="nav" aria-label={m.nav.label}>
        {NAV.map(({ href, label }) => (
          <Link key={href} href={href} aria-current={isCurrent(pathname, href) && !(href === "/" && thread) ? "page" : undefined} onClick={() => setOpen(false)}>{label}</Link>
        ))}
        {recent.length ? (
          <div className="recent">
            <p className="nav-label">{m.nav.recent}</p>
            {recent.map((t) => (
              <Link key={t.id} href={`/?thread=${encodeURIComponent(t.id)}`} aria-current={thread === t.id ? "page" : undefined} className="truncate" onClick={() => setOpen(false)}>
                {cellText(t.title, 60) || "Untitled chat"}
              </Link>
            ))}
          </div>
        ) : null}
      </nav>
      <div className="foot">
        {userName ? <b>{cellText(userName)}</b> : null}
        {email ? <span className="truncate">{cellText(email)}</span> : null}
        <button type="button" className="link-btn small" onClick={() => void signOut()} disabled={busy}>{m.nav.signOut}</button>
      </div>
    </aside>
  );
  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="topbar">
        <b>{m.product}</b>
        <button type="button" className="btn sm" aria-expanded={open} aria-controls="sidebar" onClick={() => setOpen((v) => !v)}>{m.nav.menu}</button>
      </div>
      {sidebar}
      <main className="main" id="main" tabIndex={-1}>{children}</main>
    </div>
  );
}
