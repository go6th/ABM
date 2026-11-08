"use client";
// The model chip and its menu (owner decision 2026-10-14), as claude.ai shows a model picker:
// the chip names the model ("Fable 5.1", or "Research: Opus 5.5") with a down arrow; the menu
// lists each model's name and one plain line. Beside it, the effort chip ("Effort: Medium",
// medium or high, owner decision 2026-10-20). A model that cannot be chosen is greyed out with
// its reason ("Not set up", "Not enabled for your workspace"), so nothing fails at Send.
// Ask (owner request 2026-11-08) keeps the effort inside the model menu instead: the chip reads
// "Fable 5.1  Medium" and the menu ends with an Effort switch; picking an effort keeps it open.
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ModelChoices } from "@/lib/gen/api.gen";
import { useSharedModelChoices } from "@/lib/model-choices";

/** The model menus' data: the copy the workspace layout loaded (the user's saved models, at
 * first paint), else fetched once; null until it arrives, "failed" when it cannot. */
export function useModelChoicesState(): ModelChoices | null | "failed" {
  const shared = useSharedModelChoices();
  const [fetched, setFetched] = useState<ModelChoices | null | "failed">(null);
  const need = !shared?.choices;
  useEffect(() => {
    if (!need) return;
    let live = true;
    api<ModelChoices>("GET", "/v1/models/choices")
      .then((c) => { if (live) { setFetched(c); shared?.setChoices(c); } })
      .catch(() => { if (live) setFetched("failed"); });
    return () => { live = false; };
  }, [need, shared]);
  return shared?.choices ?? fetched;
}

/** The same data for menus that simply stay hidden until it arrives (null when loading or failed). */
export function useModelChoices(): ModelChoices | null {
  const state = useModelChoicesState();
  return state === "failed" ? null : state;
}

const EFFORT_NAMES: Record<string, string> = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max" };
const EFFORT_LINES: Record<string, string> = {
  low: "Quickest answers, for simple lookups",
  medium: "Balanced, right for most questions",
  high: "Thinks longer on hard questions. Slower, costs more",
  xhigh: "Thinks much longer. Slower, costs more",
  max: "The deepest thinking. Slowest, costs the most",
};
const effortName = (e: string) => EFFORT_NAMES[e] ?? e;

function Check() {
  return <svg className="tick" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" /></svg>;
}

export function ModelMenu({ choices, value, onChange, role, menuDown = false, className = "chipa", effort = null, onEffort }: {
  choices: ModelChoices;
  value: string;
  onChange: (id: string) => void;
  role?: "Research" | "Analysis";
  menuDown?: boolean;
  className?: string;
  /** with ``onEffort``: the effort is chosen inside this menu (Ask) instead of its own chip */
  effort?: string | null;
  onEffort?: (effort: string) => void;
}) {
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const current = choices.models.find((m) => m.id === value);
  const label = current?.label ?? value;
  const withEffort = Boolean(effort && onEffort && choices.efforts.length > 1);

  useEffect(() => {
    if (!open) return;
    const items = () => Array.from(wrap.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]:not([aria-disabled="true"])') ?? []);
    (wrap.current?.querySelector<HTMLElement>('[role="menuitemradio"][aria-checked="true"]') ?? items()[0])?.focus();
    const onDown = (event: MouseEvent) => { if (wrap.current && !wrap.current.contains(event.target as Node)) setOpen(false); };
    const onKey = (event: KeyboardEvent) => {
      // handled here, so a pop-up around the menu stays open (Modal skips a prevented Escape)
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); button.current?.focus(); }
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

  // a menu taller than the room below opens on the roomier side and is capped to it; the model
  // list then scrolls while the effort switch stays in view (data-flip is read by the stylesheet;
  // measured once it is drawn, before it is painted)
  useLayoutEffect(() => {
    if (!open) return;
    const menu = wrap.current?.querySelector<HTMLElement>(".model-menu");
    const chip = button.current?.getBoundingClientRect();
    if (!menu || !chip) return;
    const height = menu.getBoundingClientRect().height;
    const below = window.innerHeight - chip.bottom - 14;
    const above = chip.top - 14;
    const down = menuDown ? !(height > below && above > below) : height > above && below > above;
    if (down !== menuDown) menu.dataset.flip = down ? "down" : "up";
    const room = down ? below : above;
    if (height > room) menu.style.maxHeight = `${Math.max(room, 200)}px`;
  }, [open, menuDown]);

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
        aria-label={withEffort
          ? `Model: ${current?.name ?? value}, effort ${effortName(effort!)}. Change model or effort`
          : `${role ? `${role} model` : "Model"}: ${current?.name ?? value}. Change model`}
        onClick={() => setOpen((v) => !v)}
      >
        {role ? `${role}: ${label}` : label}
        {withEffort ? <span className="chip-effort">{effortName(effort!)}</span> : null}
        <svg className="chev" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M3 4.5l3 3 3-3" /></svg>
      </button>
      {open ? (
        <div className={`menu model-menu ${withEffort ? "with-effort" : ""} ${menuDown ? "down" : ""}`} role="menu" id={menuId} aria-label={role ? `${role} model` : withEffort ? "Model and effort" : "Model"}>
          <div role="group" aria-label="Model">
            {withEffort ? <p className="mm-head" aria-hidden="true">Model</p> : null}
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
                {withEffort && m.id === value ? <Check /> : null}
              </button>
            ))}
          </div>
          {withEffort ? (
            <div className="mm-effort" role="group" aria-label="Effort">
              <p className="mm-head" aria-hidden="true">Effort</p>
              <div className="mm-seg">
                {choices.efforts.map((e) => (
                  <button key={e} type="button" role="menuitemradio" aria-checked={e === effort} onClick={() => onEffort!(e)}>
                    {effortName(e)}
                  </button>
                ))}
              </div>
              <p className="mm-note" aria-live="polite">{EFFORT_LINES[effort!] ?? "How hard the model thinks"}</p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

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
  const name = effortName;
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

/** "Research: Opus 5.5" and its effort, changeable before Start. The analysis model and effort
 * come from Settings > Models (2026-11-07: four chips on every start form were too many). */
export function ResearchModelChips({ state }: { state: ReturnType<typeof useResearchModels> }) {
  if (!state.choices || !state.picked) return null;
  const { choices, picked } = state;
  return (
    <div className="row-12 wrap" role="group" aria-label="Models for this research">
      <ModelMenu choices={choices} value={picked.research} onChange={state.setResearch} role="Research" menuDown className="chipa" />
      <EffortChip efforts={choices.efforts} value={picked.researchEffort} onChange={state.setResearchEffort} role="Research" />
    </div>
  );
}
