// Model preferences (owner decision 2026-10-14): the user's default research model (pulling and
// extraction) and analysis model (answers, file analysis and the deliverables). Remembered on the
// user's own profile; a model the workspace cannot use is greyed out with the reason.
import type { Metadata } from "next";
import { ModelPreferences } from "@/components/ModelPreferences";

export const metadata: Metadata = { title: "Model preferences · Account Intelligence" };

export default function Page() {
  return (
    <div className="pg narrow">
      <div className="head">
        <div className="ttl">
          <h1>Model preferences</h1>
          <div className="sub">The models your research and answers use unless you choose another one for a single question or research.</div>
        </div>
      </div>
      <ModelPreferences />
    </div>
  );
}
