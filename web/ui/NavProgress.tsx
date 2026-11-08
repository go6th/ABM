"use client";
// Click feedback for page changes (2026-11-07): a thin bar across the top while the next page
// loads, and a pending mark (data-pending) on the link that was clicked, so every click shows it
// was heard. It starts on a plain left click on a same-site link that goes to another address
// and ends when the address changes; a page that never arrives clears it after 12 s. Downloads,
// new tabs and links to the same page are left alone. The <details> menus (.dmenu) close on the
// change, on a click outside them and on Escape, and only one is open at a time.
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

const GIVE_UP_MS = 12_000;

function navigatesAway(event: MouseEvent): HTMLAnchorElement | null {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
  const link = (event.target as Element | null)?.closest?.("a[href]");
  if (!(link instanceof HTMLAnchorElement)) return null;
  if ((link.target && link.target !== "_self") || link.hasAttribute("download")) return null;
  const url = new URL(link.href, window.location.href);
  if (url.origin !== window.location.origin) return null;
  // files and API reads are downloads, not pages
  if (url.pathname.startsWith("/v1/") || url.pathname.startsWith("/api/")) return null;
  if (url.pathname === window.location.pathname && url.search === window.location.search) return null;
  return link;
}

export function NavProgress() {
  const pathname = usePathname();
  const params = useSearchParams();
  const [state, setState] = useState<"idle" | "loading" | "done">("idle");
  const pending = useRef<HTMLAnchorElement | null>(null);
  const giveUp = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const clear = () => {
      pending.current?.removeAttribute("data-pending");
      pending.current = null;
      if (giveUp.current) clearTimeout(giveUp.current);
      giveUp.current = null;
    };
    // capture phase: the Link's own handler prevents the default afterwards
    const onClick = (event: MouseEvent) => {
      const link = navigatesAway(event);
      if (!link) return;
      clear();
      pending.current = link;
      link.setAttribute("data-pending", "true");
      setState("loading");
      giveUp.current = setTimeout(() => { clear(); setState("idle"); }, GIVE_UP_MS);
    };
    const menus = () => Array.from(document.querySelectorAll<HTMLDetailsElement>("details.dmenu[open]"));
    const onDown = (event: PointerEvent) => {
      for (const menu of menus()) if (!menu.contains(event.target as Node)) menu.open = false;
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const open = menus();
      if (!open.length) return;
      event.preventDefault();
      for (const menu of open) menu.open = false;
      open[open.length - 1]!.querySelector<HTMLElement>("summary")?.focus();
    };
    const onToggle = (event: Event) => {
      const opened = event.target;
      if (!(opened instanceof HTMLDetailsElement) || !opened.open || !opened.classList.contains("dmenu")) return;
      for (const menu of menus()) if (menu !== opened) menu.open = false;
    };
    document.addEventListener("click", onClick, true);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    document.addEventListener("toggle", onToggle, true);
    return () => {
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("toggle", onToggle, true);
      clear();
    };
  }, []);

  const query = params.toString();
  useEffect(() => {
    document.querySelectorAll("details.dmenu[open]").forEach((menu) => menu.removeAttribute("open"));
    if (!pending.current) return;
    pending.current.removeAttribute("data-pending");
    pending.current = null;
    if (giveUp.current) clearTimeout(giveUp.current);
    giveUp.current = null;
    setState("done");
    const idle = setTimeout(() => setState("idle"), 320);
    return () => clearTimeout(idle);
  }, [pathname, query]);

  return <div className="nav-progress" data-state={state} aria-hidden="true"><i /></div>;
}
