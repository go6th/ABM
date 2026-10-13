"use client";
// The account title line: name, last researched, the refresh schedule line and Refresh from web
// (a deep run, confirmed first and sent with one idempotency key). While a run is open the
// steps and the checkpoint card show here.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { m } from "@/lib/messages";
import { useSchedule, writeSchedule, type Cadence } from "@/lib/schedule";
import { RunPanel } from "./RunSteps";

const CADENCES: Cadence[] = ["3d", "weekly", "2w", "monthly", "off"];

function ScheduleLine({ domain }: { domain: string }) {
  const { cadence, auto_continue: auto } = useSchedule(domain);
  const [saved, setSaved] = useState(false);
  const save = (next: { cadence?: Cadence; auto_continue?: boolean }) => {
    writeSchedule(domain, { cadence: next.cadence ?? cadence, auto_continue: next.auto_continue ?? auto });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };
  return (
    <div className="row small" role="group" aria-label={m.account.scheduleLabel}>
      <label htmlFor="cadence" className="muted">{m.account.scheduleLabel}</label>
      <select id="cadence" className="select inline" value={cadence} onChange={(e) => save({ cadence: e.target.value as Cadence })}>
        {CADENCES.map((c) => <option key={c} value={c}>{m.account.cadence[c]}</option>)}
      </select>
      <label className="check"><input type="checkbox" checked={auto} disabled={cadence === "off"} onChange={(e) => save({ auto_continue: e.target.checked })} />{m.account.autoContinue}</label>
      {saved ? <span className="status ok plain" role="status">{m.account.scheduleSaved}</span> : null}
      <span className="muted">{m.account.scheduleNote}</span>
    </div>
  );
}

function RefreshDialog({ domain, disabled, onStarted }: { domain: string; disabled: boolean; onStarted: (runId: string) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const inFlight = useRef(false);
  const confirm = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    key.current ??= newIdempotencyKey();
    try {
      const created = await api<{ run_id: string }>("POST", "/v1/runs", { domain, depth: "deep" }, { "Idempotency-Key": key.current });
      setOpen(false);
      key.current = null;
      onStarted(created.run_id);
    } catch (cause) {
      setError(cause instanceof ApiError && cause.status === 409 ? m.ask.deepBusy : m.ask.failed);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  return (
    <Dialog.Root open={open} onOpenChange={(v) => { setOpen(v); if (!v) { setError(null); key.current = null; } }}>
      <Dialog.Trigger className="btn primary" disabled={disabled}>{m.account.refresh}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog">
          <Dialog.Title asChild><h2>{m.account.refresh}</h2></Dialog.Title>
          <Dialog.Description>A deep search goes to the web and refreshes this account. It pauses for your approval at each checkpoint.</Dialog.Description>
          {error ? <p className="error-text small" role="alert">{error}</p> : null}
          <div className="actions end">
            <Dialog.Close className="btn">Stay local</Dialog.Close>
            <button type="button" className="btn primary" disabled={busy} onClick={() => void confirm()}>Start deep search</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function AccountHeader({ domain, name, researched, activeRunId, activeStatus, fileMode }: {
  domain: string; name: string; researched: string; activeRunId: string | null; activeStatus: string | null; fileMode: boolean;
}) {
  const router = useRouter();
  const [runId, setRunId] = useState<string | null>(activeRunId);
  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <p className="crumb"><Link href="/accounts">{m.account.back}</Link></p>
          <h1>{name}</h1>
          <p className="sub">{domain !== name ? `${domain}. ` : ""}{researched}{fileMode ? `. ${m.account.fromFiles}` : ""}</p>
        </div>
        <div className="actions">
          <Link className="btn" href={`/?account=${encodeURIComponent(domain)}`}>{m.account.ask}</Link>
          <RefreshDialog domain={domain} disabled={runId !== null} onStarted={(id) => { setRunId(id); router.refresh(); }} />
        </div>
      </header>
      <ScheduleLine domain={domain} />
      {runId ? (
        <section className="section" aria-label={m.run.title}>
          <h2>{activeStatus?.toUpperCase() === "AWAITING_APPROVAL" ? m.run.paused : m.run.title}</h2>
          <RunPanel runId={runId} compact />
        </section>
      ) : null}
    </div>
  );
}
