"use client";
// Model selection as one pop-up (2026-11-08, owner: "keep model selection properly designed in a
// pop-up", on Auto refresh and in Settings). ModelSummary is the card a page shows: the research
// and analysis models in use now, each with its effort, and "Change models". The pop-up
// (ModelPickerDialog) lists every model for each role as a card, greyed out with its reason when
// the workspace cannot use it, with the effort below. Nothing is saved until Save, which sends the
// four choices at once (PUT /v1/me/model-preferences) and shares them with every open screen
// (lib/model-choices). Scheduled refreshes run with the account owner's models, so the same card
// on Auto refresh shows which models those refreshes use.
import { useId, useState, type ReactNode } from "react";
import { useModelChoicesState } from "@/components/ModelMenu";
import { api, errorText } from "@/lib/api";
import type { ModelChoices } from "@/lib/gen/api.gen";
import { useSharedModelChoices } from "@/lib/model-choices";
import { Modal } from "@/ui/Modal";
import { announce } from "@/ui/Toaster";
import { Pill, StateCard } from "@/ui/design";

type Role = "research" | "analysis";

export const ROLES: Array<{ key: Role; title: string; text: string }> = [
  { key: "research", title: "Research model", text: "Reads public sources and pulls out the facts." },
  { key: "analysis", title: "Analysis model", text: "Writes answers, reads your files and writes the deliverables." },
];
export const EFFORT_NAMES: Record<string, string> = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max" };
const EFFORT_LINES: Record<string, string> = { low: "Fastest, cheapest", medium: "Balanced", high: "Slower, more thorough", xhigh: "Very thorough", max: "Everything it has" };

interface Draft { research: string; analysis: string; research_effort: string; analysis_effort: string }

function draftOf(c: ModelChoices): Draft {
  return { research: c.research, analysis: c.analysis, research_effort: c.research_effort, analysis_effort: c.analysis_effort };
}

function nameOf(choices: ModelChoices, id: string): string {
  return choices.models.find((x) => x.id === id)?.name ?? id;
}

export function ModelPickerDialog({ choices, onClose }: { choices: ModelChoices; onClose: () => void }) {
  const shared = useSharedModelChoices();
  const saved = draftOf(choices);
  const [draft, setDraft] = useState<Draft>(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changed = (Object.keys(saved) as Array<keyof Draft>).filter((k) => draft[k] !== saved[k]);
  const atDefaults = draft.research === choices.default_research && draft.analysis === choices.default_analysis;

  const set = (patch: Partial<Draft>) => { setError(null); setDraft((d) => ({ ...d, ...patch })); };

  const save = async () => {
    if (busy || !changed.length) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api<ModelChoices>("PUT", "/v1/me/model-preferences", {
        research_model: draft.research,
        analysis_model: draft.analysis,
        research_effort: draft.research_effort,
        analysis_effort: draft.analysis_effort,
      });
      shared?.setChoices(next); // every open screen now starts with these
      announce("Models saved");
      onClose();
    } catch (cause) {
      setError(errorText(cause, "Not saved. Try again."));
      setBusy(false);
    }
  };

  return (
    <Modal title="Choose your models" wide busy={busy} onClose={onClose}>
      <p className="mut sm-text">Every research, refresh and answer starts with these models. You can still change the research model for one research when you start it.</p>
      {ROLES.map((r) => {
        const current = draft[r.key];
        const effort = draft[`${r.key}_effort`];
        const wsDefault = choices[`default_${r.key}`];
        return (
          <section key={r.key} className="mpk-role stack-12" aria-labelledby={`mpk-${r.key}`}>
            <div className="mpk-role-head">
              <h3 id={`mpk-${r.key}`}>{r.title}</h3>
              <span className="mut sm-text">{r.text}</span>
            </div>
            <div className="mp-grid" role="radiogroup" aria-labelledby={`mpk-${r.key}`}>
              {choices.models.map((x) => {
                const on = x.id === current;
                return (
                  <button
                    key={x.id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    aria-disabled={!x.available || undefined}
                    disabled={busy}
                    className={`mp-opt${on ? " on" : ""}${x.available ? "" : " off"}`}
                    onClick={() => { if (x.available && !on) set({ [r.key]: x.id } as Partial<Draft>); }}
                  >
                    <span className="mp-opt-top">
                      <span className="mp-name">{x.name}</span>
                      {on ? <span className="mp-check" aria-hidden="true">✓</span> : null}
                    </span>
                    <span className="mp-line">{x.available ? x.line : x.reason}</span>
                    {x.id === wsDefault ? <span className="mp-def">Workspace default</span> : null}
                  </button>
                );
              })}
            </div>
            {choices.efforts.length > 1 ? (
              <div className="mp-effort">
                <span className="lbl" aria-hidden="true">Effort</span>
                <div className="seg wide" role="radiogroup" aria-label={`${r.title} effort`}>
                  {choices.efforts.map((e) => (
                    <button key={e} type="button" role="radio" aria-checked={e === effort} disabled={busy} title={EFFORT_LINES[e] ?? e} onClick={() => set({ [`${r.key}_effort`]: e } as Partial<Draft>)}>
                      {EFFORT_NAMES[e] ?? e}
                    </button>
                  ))}
                </div>
                <span className="mut xs-text">{EFFORT_LINES[effort] ?? ""}. Higher effort is slower and costs more.</span>
              </div>
            ) : null}
          </section>
        );
      })}
      {error ? <p className="notice error sm-text" role="alert">{error}</p> : null}
      <div className="mpk-foot">
        <button type="button" className="link-btn small" disabled={busy || atDefaults} onClick={() => set({ research: choices.default_research, analysis: choices.default_analysis })}>
          Use the workspace defaults
        </button>
        <span className="modal-actions">
          <button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="button" className="btn primary" disabled={!changed.length} aria-busy={busy || undefined} onClick={() => void save()}>
            {busy ? "Saving…" : "Save models"}
          </button>
        </span>
      </div>
    </Modal>
  );
}

/** The models in use now, as a card with "Change models" (Settings, Models and Auto refresh). */
export function ModelSummary({ title = "Your models", intro, note }: { title?: string; intro?: ReactNode; note?: ReactNode }) {
  const state = useModelChoicesState();
  const [open, setOpen] = useState(false);
  const titleId = useId();

  if (state === "failed") return <StateCard title="Model preferences could not be loaded" body="Reload the page in a moment." action={<a className="btn" href="">Reload</a>} />;
  if (!state) return <div className="stack-14"><div className="skel block" /></div>;
  if (!state.models.length) return <StateCard title="No models are available" body="Ask your administrator to set up a model." />;
  const choices = state;

  return (
    <section className="card pad-lg mps" aria-labelledby={titleId}>
      <div className="mps-head">
        <div className="stack-4">
          <h2 id={titleId}>{title}</h2>
          {intro ? <p className="mut sm-text">{intro}</p> : null}
        </div>
        <button type="button" className="btn primary" aria-haspopup="dialog" onClick={() => setOpen(true)}>Change models</button>
      </div>
      <dl className="mps-list">
        {ROLES.map((r) => {
          const id = choices[r.key];
          const effort = choices[`${r.key}_effort`];
          return (
            <div key={r.key} className="mps-row">
              <dt>
                <span className={r.key === "research" ? "mps-ic" : "mps-ic lav"} aria-hidden="true">{r.key === "research" ? "R" : "A"}</span>
                <span className="stack-4">
                  <span className="strong">{r.title}</span>
                  <span className="mut xs-text">{r.text}</span>
                </span>
              </dt>
              <dd>
                <span className="mps-model">{nameOf(choices, id)}</span>
                {choices.efforts.length > 1 ? <Pill tone="run" small>{EFFORT_NAMES[effort] ?? effort} effort</Pill> : null}
                {id === choices[`default_${r.key}`] ? <span className="mut xs-text">Workspace default</span> : null}
              </dd>
            </div>
          );
        })}
      </dl>
      {note ? <div className="mps-note">{note}</div> : null}
      {open ? <ModelPickerDialog choices={choices} onClose={() => setOpen(false)} /> : null}
    </section>
  );
}
