"use client";
// WebOffer: shown inline when a question needs newer data than the workspace holds. Confirming
// is billed, so the button disables on the first click and the caller sends one Idempotency-Key
// for the whole attempt: a double click or a retry never charges twice.
import { useRef, useState } from "react";
import { Globe } from "lucide-react";
import { m } from "@/lib/messages";
import { newIdempotencyKey } from "@/lib/api";

export interface Offer { what: string; scope: string; minutes: number; credits: number; left: number }

export function WebOffer({
  offer, onConfirm, onStay,
}: { offer: Offer; onConfirm: (idempotencyKey: string) => Promise<void>; onStay: () => void }) {
  const key = useRef<string | null>(null);
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const confirm = async () => {
    // A ref, not state: two clicks in the same tick must not both pass.
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    key.current ??= newIdempotencyKey();
    try {
      await onConfirm(key.current);
    } catch {
      inFlight.current = false;
      setBusy(false);
    }
  };
  return (
    <section className="ws-offer" aria-label={m.offer.title}>
      <h3><Globe size={16} strokeWidth={1.6} aria-hidden="true" />{m.offer.title}</h3>
      <p>{offer.what}</p>
      <div className="facts">
        <span>{m.offer.scope} <b>{offer.scope}</b></span>
        <span>{m.offer.time} <b>{offer.minutes} min</b></span>
        <span>{m.offer.credits} <b>{offer.credits}</b></span>
        <span>{m.offer.left} <b>{offer.left}</b></span>
      </div>
      <div className="actions">
        <button type="button" className="ws-btn web-solid" disabled={busy} onClick={() => void confirm()}>{m.offer.confirm}</button>
        <button type="button" className="ws-btn quiet" onClick={onStay}>{m.offer.stay}</button>
      </div>
    </section>
  );
}
