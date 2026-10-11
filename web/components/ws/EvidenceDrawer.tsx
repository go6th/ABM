"use client";
// EvidenceDrawer: opens from a citation chip. Focus moves in on open, Esc closes, and focus goes
// back to the chip that opened it. A right side panel on wide screens, a bottom sheet at 1100px
// and below (CSS). Rendered inside the workspace root so it inherits the theme tokens.
import * as Dialog from "@radix-ui/react-dialog";
import { ExternalLink, X } from "lucide-react";
import { GradeMark } from "./GradeMark";
import type { Evidence } from "./evidence";
import { m } from "@/lib/messages";
import { LINK_REL } from "@/lib/render";

export function wsRoot(): HTMLElement | undefined {
  return typeof document === "undefined" ? undefined : document.getElementById("ws-root") ?? undefined;
}

function Excerpt({ text, span }: { text: string; span: [number, number] | null }) {
  if (!span || span[0] < 0 || span[1] > text.length || span[0] >= span[1]) return <p className="excerpt">{text}</p>;
  return (
    <p className="excerpt">
      {text.slice(0, span[0])}<mark>{text.slice(span[0], span[1])}</mark>{text.slice(span[1])}
    </p>
  );
}

export function EvidenceDrawer({
  items, open, onOpenChange, onPick, returnFocus,
}: {
  items: Evidence[];
  open: number | null;
  onOpenChange: (n: number | null) => void;
  onPick: (n: number) => void;
  returnFocus: HTMLElement | null;
}) {
  const current = items.find((e) => e.n === open) ?? null;
  return (
    <Dialog.Root open={current !== null} onOpenChange={(v) => { if (!v) onOpenChange(null); }}>
      <Dialog.Portal container={wsRoot()}>
        <Dialog.Overlay className="ws-drawer-overlay" />
        <Dialog.Content
          className="ws-drawer"
          aria-describedby={undefined}
          onCloseAutoFocus={(e) => { if (returnFocus) { e.preventDefault(); returnFocus.focus(); } }}
        >
          {current ? (
            <>
              <header>
                <Dialog.Title>{m.evidence.source(current.n)}</Dialog.Title>
                <Dialog.Close className="ws-icon-btn" aria-label={m.evidence.close}><X size={18} strokeWidth={1.6} /></Dialog.Close>
              </header>
              <div className="body">
                <div>
                  <GradeMark grade={current.grade} />
                  {current.reason ? <p className="reason ws-small">{current.reason}</p> : null}
                </div>
                <div>
                  <p className="src-title">{current.title}</p>
                  <p className="ws-small ws-muted">
                    {current.retrieved ? m.evidence.retrieved(current.sourceName, current.retrieved) : current.sourceName}
                  </p>
                </div>
                {current.excerpt ? <Excerpt text={current.excerpt} span={current.span} /> : null}
                {current.url ? (
                  <a className="open" href={current.url} target="_blank" rel={`${LINK_REL} nofollow`}>
                    {m.evidence.open} <ExternalLink size={14} strokeWidth={1.6} aria-hidden="true" />
                  </a>
                ) : <span className="archive">{m.evidence.archive}</span>}
                {items.length > 1 ? (
                  <div className="others">
                    <p className="ws-label ws-muted">{m.evidence.others}</p>
                    {items.filter((e) => e.n !== current.n).map((e) => (
                      <button key={e.n} type="button" onClick={() => onPick(e.n)}>
                        <span className="ws-small ws-muted">{e.n}</span>
                        <GradeMark grade={e.grade} showLabel={false} />
                        <span>{e.title}</span>
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            </>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
