"use client";
// Model preferences (owner decision 2026-10-14): the user's default research model (pulling and
// extraction) and analysis model (answers, file analysis, deliverables), each with its effort,
// saved to the user's own profile. Since 2026-11-08 the page shows the models in use as one card
// and they are changed in the model pop-up (components/ModelPicker), the same one Auto refresh
// opens; nothing is saved until Save.
import { ModelSummary } from "@/components/ModelPicker";

export function ModelPreferences() {
  return (
    <ModelSummary
      intro="Every research, refresh and answer starts with these. Scheduled refreshes use them too."
      note={<p className="mut xs-text">A model your workspace cannot use is shown greyed out in the pop-up with the reason. Higher effort is slower and costs more.</p>}
    />
  );
}
