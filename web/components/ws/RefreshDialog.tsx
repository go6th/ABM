"use client";
// Refresh from web: a billed run, so it confirms first, sends one Idempotency-Key per attempt
// and disables on the first click. The server decides credits and limits; this mirrors them.
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { Globe } from "lucide-react";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { m } from "@/lib/messages";

type Depth = "standard" | "deep";

export function RefreshDialog({ domain, disabled, pursuing }: { domain: string; disabled: boolean; pursuing: string | null }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [depth, setDepth] = useState<Depth>("standard");
  const [forClient, setForClient] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const inFlight = useRef(false);
  const confirm = async () => {
    // A ref, not state: two clicks in the same tick must not both pass.
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    key.current ??= newIdempotencyKey();
    try {
      await api("POST", "/v1/runs", { domain, depth, pursuing_client: forClient }, { "Idempotency-Key": key.current });
      setOpen(false);
      key.current = null;
      router.refresh();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) setError(m.refresh.busy);
      else if (cause instanceof ApiError && (cause.status === 402 || cause.type.includes("budget") || cause.type.includes("wallet"))) setError(m.refresh.noCredits);
      else setError(m.refresh.failed);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  return (
    <Dialog.Root open={open} onOpenChange={(v) => { setOpen(v); if (!v) { setError(null); key.current = null; } }}>
      <Dialog.Trigger className="btn web" disabled={disabled}>
        <Globe size={16} strokeWidth={1.6} aria-hidden="true" />{m.workspace.refresh}
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog">
          <Dialog.Title>{m.refresh.title}</Dialog.Title>
          <Dialog.Description className="desc">{m.refresh.desc}</Dialog.Description>
          <fieldset className="choices">
            <legend className="sr-only">{m.refresh.title}</legend>
            {(["standard", "deep"] as const).map((d) => (
              <label key={d}>
                <input type="radio" name="depth" value={d} checked={depth === d} onChange={() => setDepth(d)} />
                <span>
                  <span className="strong">{d === "standard" ? m.refresh.standard : m.refresh.deep}</span>
                  <span className="hint">{d === "standard" ? m.refresh.standardHint : m.refresh.deepHint}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {pursuing ? (
            <label className="check">
              <input type="checkbox" checked={forClient} onChange={(e) => setForClient(e.target.checked)} />
              {m.refresh.pursuing(pursuing)}
            </label>
          ) : null}
          {error ? <p className="banner error t-footnote gap-top" role="alert">{error}</p> : null}
          <div className="actions">
            <Dialog.Close className="btn quiet">{m.refresh.stay}</Dialog.Close>
            <button type="button" className="btn web-solid" disabled={busy} onClick={() => void confirm()}>{m.refresh.confirm}</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
