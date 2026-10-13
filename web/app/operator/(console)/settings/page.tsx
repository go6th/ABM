"use client";
// Settings: checkpoint size, model routing for Phase 1 and Phase 2, the rate table and the skill
// version. Checkpoint size and model routing are kept as platform flags; the skill registry and
// rate table need their own endpoints and are shown as not connected.
import { useState } from "react";
import { operatorRequest } from "@/lib/operator-api";
import { useOperatorApi } from "../useOperatorApi";
import { Field, Loading, Notice, PageHead } from "@/ui/primitives";

const FLAGS = {
  checkpoint: "checkpoint_unit",
  phase1: "model_phase1",
  phase2: "model_phase2",
} as const;

function ruleValue(rule: Record<string, unknown> | undefined): string {
  if (!rule) return "";
  const v = rule.value ?? rule.default ?? rule.enabled;
  return v === undefined || v === null ? "" : String(v);
}

export default function Settings() {
  const flags = useOperatorApi("GET /operator/v1/flags");
  const keys = useOperatorApi("GET /operator/v1/keys");
  const [edits, setEdits] = useState<{ checkpoint?: string; phase1?: string; phase2?: string }>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const by = new Map((flags.data?.flags ?? []).map((f) => [f.flag, f.rule]));
  const checkpoint = edits.checkpoint ?? (ruleValue(by.get(FLAGS.checkpoint)) || "1000");
  const phase1 = edits.phase1 ?? ruleValue(by.get(FLAGS.phase1));
  const phase2 = edits.phase2 ?? ruleValue(by.get(FLAGS.phase2));
  const setCheckpoint = (v: string) => setEdits((e) => ({ ...e, checkpoint: v }));
  const setPhase1 = (v: string) => setEdits((e) => ({ ...e, phase1: v }));
  const setPhase2 = (v: string) => setEdits((e) => ({ ...e, phase2: v }));
  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const n = Number(checkpoint);
      if (!Number.isFinite(n) || n <= 0) throw new Error("Checkpoint size must be a number above zero.");
      await operatorRequest("POST /operator/v1/flags", { body: { flag: FLAGS.checkpoint, reason_code: "settings_save", rule: { value: Math.round(n) } } });
      if (phase1.trim()) await operatorRequest("POST /operator/v1/flags", { body: { flag: FLAGS.phase1, reason_code: "settings_save", rule: { value: phase1.trim() } } });
      if (phase2.trim()) await operatorRequest("POST /operator/v1/flags", { body: { flag: FLAGS.phase2, reason_code: "settings_save", rule: { value: phase2.trim() } } });
      setMessage("Saved. Changes apply to every client's next run.");
      setEdits({});
      flags.reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "The settings were not saved. Try again.");
    } finally {
      setBusy(false);
    }
  };
  if (flags.error) return <Notice kind="error">{flags.error.detail}</Notice>;
  if (!flags.data) return <Loading />;
  return (
    <>
      <PageHead title="Settings" sub="Applied to every client. No caps: these set how work runs, not how much a user may do." />
      <form className="form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <h2>Checkpoints</h2>
        <Field id="checkpoint" label="Checkpoint size (units)" hint="A run pauses for the user's approval after this many units. Users choose Continue, Continue 5 times, Auto continue or Stop.">
          <input id="checkpoint" className="input narrow" inputMode="numeric" value={checkpoint} onChange={(e) => setCheckpoint(e.target.value)} />
        </Field>
        <h2>Model routing</h2>
        <Field id="phase1" label="Phase 1 (pulls)" hint="The lower cost model that pulls everything into files and never interprets.">
          <input id="phase1" className="input wide" value={phase1} onChange={(e) => setPhase1(e.target.value)} />
        </Field>
        <Field id="phase2" label="Phase 2 (sense check and build)" hint="The stronger model for the sense check and the build.">
          <input id="phase2" className="input wide" value={phase2} onChange={(e) => setPhase2(e.target.value)} />
        </Field>
        {message ? <p className="small" role="status">{message}</p> : null}
        <div className="actions"><button type="submit" className="btn primary" disabled={busy}>Save settings</button></div>
      </form>
      <section className="section">
        <h2>Rate table</h2>
        <p className="muted">Not connected. The rate table that converts tool spend to units needs its own endpoint.</p>
      </section>
      <section className="section">
        <h2>Skill version</h2>
        <p className="muted">Not connected. Upload, activate and roll back need the skill registry endpoints.</p>
      </section>
      <section className="section">
        <h2>Data tool keys</h2>
        {keys.error ? <p className="error-text small">{keys.error.detail}</p> : !keys.data ? <Loading /> : keys.data.keys.length === 0 ? <p className="muted">No keys recorded.</p> : (
          <ul className="rows">{keys.data.keys.map((k) => <li key={`${k.provider_code}-${k.version}`}><span>{k.provider_code}</span><span className="end">Version {k.version}</span></li>)}</ul>
        )}
      </section>
    </>
  );
}
