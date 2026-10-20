"use client";
// The model chip and its menu (owner decision 2026-10-14), as claude.ai shows a model picker:
// the chip names the model ("Fable 5.1", or "Research: Opus 5.5") with a down arrow; the menu
// lists each model's name and one plain line. Beside it, the effort chip ("Effort: Medium",
// medium or high, owner decision 2026-10-20). A model that cannot be chosen is greyed out with
// its reason ("Not set up", "Not enabled for your workspace"), so nothing fails at Send.
import { useEffect, useId, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ModelChoices } from "@/lib/gen/api.gen";

/** The model menus' data, loaded once per screen: null until it arrives, "failed" when it cannot. */
export function useModelChoicesState(): ModelChoices | null | "failed" {
  const [choices, setChoices] = useState<ModelChoices | null | "failed">(null);
  useEffect(() => {
    let live = true;
    api<ModelChoices>("GET", "/v1/models/choices")
      .then((c) => { if (live) setChoices(c); })
      .catch(() => { if (live) setChoices("failed"); });
    return () => { live = false; };
  }, []);
  return choices;
}

/** The same data for menus that simply stay hidden until it arrives (null when loading or failed). */
export function useModelChoices(): ModelChoices | null {
  const state = useModelChoicesState();
  return state === "failed" ? null : state;
}

export function ModelMenu({ choices, value, onChange, role, menuDown = false, className = "chipa" }: {
  choices: ModelChoices;
  value: string;
  onChange: (id: string) => void;
  role?: "Research" | "Analysis";
  menuDown?: boolean;
  className?: string;
}) {
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const current = choices.models.find((m) => m.id === value);
  const label = current?.label ?? value;

  useEffect(() => {
    if (!open) return;
    const items = () => Array.from(wrap.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]:not([aria-disabled="true"])') ?? []);
    (wrap.current?.querySelector<HTMLElement>('[role="menuitemradio"][aria-checked="true"]') ?? items()[0])?.focus();
    const onDown = (event: MouseEvent) => { if (wrap.current && !wrap.current.contains(event.target as Node)) setOpen(false); };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); button.current?.focus(); }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        const list = items();
        const i = list.indexOf(document.activeElement as HTMLElement);
        list[(i + (event.key === "ArrowDown" ? 1 : list.length - 1)) % list.length]?.focus();
        event.preventDefault();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const pick = (id: string) => { onChange(id); setOpen(false); button.current?.focus(); };

  return (
    <div className="menu-wrap" ref={wrap}>
      <button
        ref={button}
        type="button"
        className={`${className} model-chip`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`${role ? `${role} model` : "Model"}: ${current?.name ?? value}. Change model`}
        onClick={() => setOpen((v) => !v)}
      >
        {role ? `${role}: ${label}` : label}
        <svg className="chev" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M3 4.5l3 3 3-3" /></svg>
      </button>
      {open ? (
        <div className={`menu model-menu ${menuDown ? "down" : ""}`} role="menu" id={menuId} aria-label={role ? `${role} model` : "Model"}>
          {choices.models.map((m) => (
            <button
              key={m.id}
              type="button"
              role="menuitemradio"
              aria-checked={m.id === value}
              aria-disabled={!m.available || undefined}
              onClick={() => { if (m.available) pick(m.id); }}
            >
              <span className="model-name">{m.name}</span>
              <span className="model-line">{m.available ? m.line : m.reason}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const EFFORT_NAMES: Record<string, string> = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max" };

/** "Effort: Medium" beside a model chip (owner decision 2026-10-20): a press moves to the next
 * effort the server offers (medium, high), like the Search on/off chip. */
export function EffortChip({ efforts, value, onChange, role, className = "chipa" }: {
  efforts: string[];
  value: string;
  onChange: (effort: string) => void;
  role?: "Research" | "Analysis";
  className?: string;
}) {
  if (efforts.length < 2) return null;
  const name = (e: string) => EFFORT_NAMES[e] ?? e;
  const next = efforts[(efforts.indexOf(value) + 1) % efforts.length] ?? value;
  return (
    <button
      type="button"
      className={className}
      aria-label={`${role ? `${role} effort` : "Effort"}: ${name(value)}. Change to ${name(next)}`}
      title="How hard the model thinks: higher is slower and costs more"
      onClick={() => onChange(next)}
    >
      {role ? `${role} effort` : "Effort"}: {name(value)}
    </button>
  );
}

/** The two model and effort choices of a research run, defaulting to the user's current ones. */
export function useResearchModels() {
  const choices = useModelChoices();
  const [research, setResearch] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [researchEffort, setResearchEffort] = useState<string | null>(null);
  const [analysisEffort, setAnalysisEffort] = useState<string | null>(null);
  const picked = choices ? {
    research: research ?? choices.research,
    analysis: analysis ?? choices.analysis,
    researchEffort: researchEffort ?? choices.research_effort,
    analysisEffort: analysisEffort ?? choices.analysis_effort,
  } : null;
  return {
    choices,
    picked,
    setResearch,
    setAnalysis,
    setResearchEffort,
    setAnalysisEffort,
    /** The confirm body's model and effort fields (empty until the menus have loaded). */
    body: picked ? {
      research_model: picked.research,
      analysis_model: picked.analysis,
      research_effort: picked.researchEffort,
      analysis_effort: picked.analysisEffort,
    } : {},
  };
}

/** "Research: Opus 5.5 · Effort: Medium" and "Analysis: …", changeable before Start. */
export function ResearchModelChips({ state }: { state: ReturnType<typeof useResearchModels> }) {
  if (!state.choices || !state.picked) return null;
  const { choices, picked } = state;
  return (
    <div className="row-12 wrap" role="group" aria-label="Models for this research">
      <ModelMenu choices={choices} value={picked.research} onChange={state.setResearch} role="Research" menuDown className="chipa" />
      <EffortChip efforts={choices.efforts} value={picked.researchEffort} onChange={state.setResearchEffort} role="Research" />
      <ModelMenu choices={choices} value={picked.analysis} onChange={state.setAnalysis} role="Analysis" menuDown className="chipa" />
      <EffortChip efforts={choices.efforts} value={picked.analysisEffort} onChange={state.setAnalysisEffort} role="Analysis" />
    </div>
  );
}
