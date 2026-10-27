"use client";
// Model preferences (owner decision 2026-10-14): the user's default research model (pulling and
// extraction) and analysis model (answers, file analysis, deliverables), each with its effort.
// Saved to the user's own profile (PUT /v1/me/model-preferences) the moment a card or an effort
// is chosen, and shared with every screen at once (lib/model-choices): Ask, New research and
// Refresh open with these models until the person changes one for a single question or research.
import { useState } from "react";
import { useModelChoicesState } from "@/components/ModelMenu";
import { api, ApiError } from "@/lib/api";
import type { ModelChoices } from "@/lib/gen/api.gen";
import { useSharedModelChoices } from "@/lib/model-choices";
import { Pill, StateCard } from "@/ui/design";

type Role = "research" | "analysis";

const ROLES: Array<{ key: Role; title: string; text: string; where: string }> = [
  { key: "research", title: "Research model", text: "Reads public sources and pulls out the facts.", where: "Used by New research, Refresh from web and scheduled refreshes." },
  { key: "analysis", title: "Analysis model", text: "Writes answers, reads your files and writes the deliverables.", where: "Used by Ask, file analysis and the account files." },
];
const EFFORT_NAMES: Record<string, string> = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max" };
const EFFORT_LINES: Record<string, string> = { low: "Fastest, cheapest", medium: "Balanced", high: "Slower, more thorough", xhigh: "Very thorough", max: "Everything it has" };

export function ModelPreferences() {
  const state = useModelChoicesState();
  const shared = useSharedModelChoices();
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);
  const choices = state === "failed" ? null : state;

  if (state === "failed") return <StateCard title="Model preferences could not be loaded" body="Reload the page in a moment." action={<a className="btn" href="/model-preferences">Reload</a>} />;
  if (!choices) return <div className="stack-14"><div className="skel block" /><div className="skel block" /></div>;
  if (!choices.models.length) return <StateCard title="No models are available" body="Ask your administrator to set up a model." />;

  const save = async (patch: Record<string, string>, label: string) => {
    setBusy(label);
    setToast(null);
    try {
      const next = await api<ModelChoices>("PUT", "/v1/me/model-preferences", patch);
      shared?.setChoices(next); // every open screen now defaults to it
      setToast({ ok: true, text: `Saved. ${label} is now your default.` });
    } catch (cause) {
      setToast({ ok: false, text: cause instanceof ApiError && cause.detail ? cause.detail : "Not saved. Try again." });
    } finally {
      setBusy(null);
    }
  };

  const isDefault = choices.research === choices.default_research && choices.analysis === choices.default_analysis;

  return (
    <div className="stack-20">
      {ROLES.map((r) => {
        const current = choices[r.key];
        const effort = choices[`${r.key}_effort`];
        const wsDefault = choices[`default_${r.key}`];
        return (
          <section key={r.key} className="card pad-lg stack-14 mp-card" aria-labelledby={`mp-${r.key}`}>
            <div className="mp-head">
              <div className="stack-4">
                <h2 id={`mp-${r.key}`}>{r.title}</h2>
                <p className="mut">{r.text}</p>
              </div>
              <span className="mut sm-text mp-where">{r.where}</span>
            </div>
            <div className="mp-grid" role="radiogroup" aria-label={r.title}>
              {choices.models.map((m) => {
                const on = m.id === current;
                return (
                  <button
                    key={m.id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    aria-disabled={!m.available || undefined}
                    disabled={busy !== null && !on}
                    className={`mp-opt${on ? " on" : ""}${m.available ? "" : " off"}`}
                    onClick={() => { if (m.available && !on) void save({ [`${r.key}_model`]: m.id }, m.name); }}
                  >
                    <span className="mp-opt-top">
                      <span className="mp-name">{m.name}</span>
                      {on ? <Pill tone="run" small>Your default</Pill> : m.id === wsDefault ? <Pill small>Workspace default</Pill> : null}
                    </span>
                    <span className="mp-line">{m.available ? m.line : m.reason}</span>
                  </button>
                );
              })}
            </div>
            {choices.efforts.length > 1 ? (
              <div className="mp-effort">
                <span className="lbl">Effort</span>
                <div className="seg wide" role="radiogroup" aria-label={`${r.title} effort`}>
                  {choices.efforts.map((e) => (
                    <button key={e} type="button" role="radio" aria-checked={e === effort} data-on={e === effort} disabled={busy !== null} title={EFFORT_LINES[e] ?? e} onClick={() => { if (e !== effort) void save({ [`${r.key}_effort`]: e }, `${EFFORT_NAMES[e] ?? e} effort`); }}>
                      {EFFORT_NAMES[e] ?? e}
                    </button>
                  ))}
                </div>
                <span className="mut sm-text">{EFFORT_LINES[effort] ?? ""}. Higher effort is slower and costs more.</span>
              </div>
            ) : null}
          </section>
        );
      })}
      <div className="row-12 wrap">
        <button type="button" className="btn" disabled={busy !== null || isDefault} onClick={() => void save({ research_model: choices.default_research, analysis_model: choices.default_analysis }, "The workspace default")}>
          Reset to workspace defaults
        </button>
        <span className="mut sm-text">You can still pick another model for one question or one research on that screen.</span>
      </div>
      {toast ? <p className={`notice ${toast.ok ? "info" : "error"} sm-text`} role={toast.ok ? "status" : "alert"}>{toast.text}</p> : null}
    </div>
  );
}
