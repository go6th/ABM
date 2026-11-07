// Model preferences (owner decision 2026-10-14): the user's default research model (pulling and
// extraction) and analysis model (answers, file analysis and the deliverables). Remembered on the
// user's own profile; a model the workspace cannot use is greyed out with the reason.
import type { Metadata } from "next";
import { ModelPreferences } from "@/components/ModelPreferences";

export const metadata: Metadata = { title: "Settings · Account Intelligence" };

export default function Page() {
  return (
    <>
      <header>
        <h2>Models</h2>
        <p className="intro">Choose once. Every screen then starts with these models, and you can still change them for a single question or research.</p>
      </header>
      <ModelPreferences />
    </>
  );
}
