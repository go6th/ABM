"use client";
// The admin console frame as designed: the navy sidebar with Overview, Clients, Users, 6th Library,
// Runs and spend and Platform defaults, View as a client, and the signed-in admin at the bottom.
// Below 760px the sidebar is an overlay menu: Escape or the scrim closes it, focus stays inside
// while open and returns to the Menu button, and the page behind is inert.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { LogoMark } from "@/ui/design";
import { OperatorNav } from "./OperatorNav";
import { OperatorSignOut } from "./OperatorSignOut";
import { useOperatorViewer } from "./OperatorSessionGate";

export function AdminShell({ children }: { children: React.ReactNode }) {
  const viewer = useOperatorViewer();
  const [open, setOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const side = useRef<HTMLElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    menuButton.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    side.current?.querySelector<HTMLElement>("a, button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close(); return; }
      if (event.key !== "Tab" || !side.current) return;
      const focusable = Array.from(side.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled])"));
      if (!focusable.length) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="topbar dark" inert={open || undefined}>
        <b>6th Admin</b>
        <button ref={menuButton} type="button" className="btn sm" aria-expanded={open} aria-controls="admin-sidebar" onClick={() => setOpen(true)}>Menu</button>
      </div>
      <button type="button" className="scrim" data-open={open} aria-label="Close menu" tabIndex={-1} onClick={close} />
      <aside ref={side} className="aside-admin" id="admin-sidebar" data-open={open} aria-label="Admin console">
        <Link className="logo" href="/operator" onClick={() => setOpen(false)}>
          <LogoMark inverse />
          <span className="name">6th Admin<span className="by">Account Intelligence</span></span>
        </Link>
        <OperatorNav onNavigate={() => setOpen(false)} />
        <div className="foot">
          <Link className="viewas" href="/operator/view" onClick={() => setOpen(false)}>View as a client</Link>
          <div className="who">
            {viewer.role === "operator" ? "Admin" : "Support"}
            <div className="em">{viewer.staffId}</div>
            <OperatorSignOut />
          </div>
        </div>
      </aside>
      <main className="main" id="main" tabIndex={-1} inert={open || undefined}>{children}</main>
    </div>
  );
}
