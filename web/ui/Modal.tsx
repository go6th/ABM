"use client";
// The shared dialog for client and operator screens. It moves focus to its first control (or
// ``initialFocus``), keeps Tab inside, closes on Escape or an intentional backdrop click, then
// returns focus to its opener or the page landmark if that opener was removed. Background content
// is inert and the page does not scroll. While ``busy`` nothing closes it, so the result is seen.
// An Escape meant for a menu open inside it is left to that menu.
import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useIsClient } from "@/lib/use-is-client";

const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])";

export function Modal({ title, onClose, busy = false, initialFocus, descriptionId, wide = false, children }: {
  title: ReactNode;
  onClose: () => void;
  busy?: boolean;
  initialFocus?: RefObject<HTMLElement | null>;
  descriptionId?: string;
  wide?: boolean;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(null);
  const downOnBackdrop = useRef(false);
  const busyNow = useRef(busy);
  const close = useRef(onClose);
  const client = useIsClient();
  const host = client ? document.querySelector<HTMLElement>(".ui-pastel") ?? document.body : null;
  const titleId = useId();

  useEffect(() => { busyNow.current = busy; close.current = onClose; });

  useEffect(() => {
    opener.current = document.activeElement;
    return () => {
      const back = opener.current;
      setTimeout(() => {
        if (back instanceof HTMLElement && back.isConnected) back.focus();
        else document.querySelector<HTMLElement>("main[tabindex], .auth-main[tabindex]")?.focus();
      }, 0);
    };
  }, []);

  useEffect(() => {
    if (!host) return;
    // The client shell portals here, while operator pages portal to body. Isolate every
    // background sibling so navigation, not only the main region, leaves the tab order.
    const backdrop = host.querySelector<HTMLElement>(":scope > .modal-backdrop");
    const background = Array.from(host.children).filter((node): node is HTMLElement =>
      node instanceof HTMLElement && node !== backdrop && !node.classList.contains("toast-region"),
    );
    const wasInert = background.map((node) => ({ node, inert: node.hasAttribute("inert") }));
    background.forEach((node) => node.setAttribute("inert", ""));
    document.documentElement.classList.add("modal-open");
    const first = initialFocus?.current ?? panel.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel.current)?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // a menu open inside the pop-up (the model menu) takes this Escape for itself
        if (event.defaultPrevented || busyNow.current || panel.current?.querySelector('[aria-haspopup="menu"][aria-expanded="true"]')) return;
        event.preventDefault();
        close.current();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) { event.preventDefault(); return; }
      const firstEl = items[0]!;
      const lastEl = items[items.length - 1]!;
      const inside = panel.current.contains(document.activeElement);
      if (!inside) { event.preventDefault(); firstEl.focus(); return; }
      if (event.shiftKey && document.activeElement === firstEl) { event.preventDefault(); lastEl.focus(); }
      else if (!event.shiftKey && document.activeElement === lastEl) { event.preventDefault(); firstEl.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      for (const { node, inert } of wasInert) if (!inert) node.removeAttribute("inert");
      document.documentElement.classList.remove("modal-open");
    };
  }, [host, initialFocus]);

  if (!host) return null;
  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(e) => { downOnBackdrop.current = e.target === e.currentTarget; }}
      onClick={(e) => { if (downOnBackdrop.current && e.target === e.currentTarget && !busyNow.current) close.current(); }}
    >
      <div ref={panel} className={wide ? "modal-panel stack-14" : "modal-panel narrow stack-14"} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1}>
        <button type="button" className="modal-close" aria-label="Close" disabled={busy} onClick={() => close.current()}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>,
    host,
  );
}

/** A yes/no question before something that cannot be undone (delete a chat, a file, an upload).
 * ``onConfirm`` may be async: the dialog shows it working, stays open on failure with the
 * message it throws, and closes when it resolves. */
export function ConfirmDialog({ title, body, confirm, working = "Working…", danger = true, onConfirm, onClose }: {
  title: string;
  body: ReactNode;
  confirm: string;
  working?: string;
  danger?: boolean;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const descriptionId = useId();
  const busyNow = useRef(false);
  const go = async () => {
    if (busyNow.current) return;
    busyNow.current = true;
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      onClose();
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message : "That did not work. Try again in a moment.");
      busyNow.current = false;
      setBusy(false);
    }
  };
  return (
    <Modal title={title} onClose={onClose} busy={busy} initialFocus={cancel} descriptionId={descriptionId}>
      <div id={descriptionId} className="sm-text">{body}</div>
      {error ? <p className="notice error" role="alert">{error}</p> : null}
      <div className="modal-actions">
        <button ref={cancel} type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="button" className={danger ? "btn primary danger" : "btn primary"} disabled={busy} aria-busy={busy || undefined} onClick={() => void go()}>{busy ? working : confirm}</button>
      </div>
    </Modal>
  );
}
