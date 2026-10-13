// Why a run stopped, in plain words, and whether it can be resumed. Pure functions: the backend
// decides the status and the reason code; this only chooses the sentence a person reads.
import type { StoredCounts } from "@/lib/gen/api.gen";

const TERMINAL = new Set(["COMPLETED", "PARTIAL", "BUDGET_EXCEEDED", "CANCELED", "FAILED"]);

// Reason codes the backend writes to finished_reason, mapped to a sentence. Unknown codes fall
// back to a generic sentence; the raw code is never shown as if it were an explanation.
const EXPLANATIONS: Record<string, string> = {
  worker_lost: "The machine running this research stopped unexpectedly. Everything it had collected is kept.",
  ProviderCreditsExhausted: "The language model account ran out of credits. Everything collected before that is kept.",
  model_credits_exhausted: "The language model account ran out of credits. Everything collected before that is kept.",
  AuthFailed: "The language model key was refused. Check the key in settings, then resume.",
  model_auth_failed: "The language model key was refused. Check the key in settings, then resume.",
  NotConfigured: "No language model key is set up. Add one in settings, then resume.",
  model_not_configured: "No language model key is set up. Add one in settings, then resume.",
  ContentRefused: "The language model declined part of this work. Everything collected before that is kept.",
  model_refused: "The language model declined part of this work. Everything collected before that is kept.",
  ContextTooLong: "One batch of sources was too large for the model. The rest was processed.",
  build_failed: "The research finished but the summary document could not be built. The findings are kept.",
  builders_not_configured: "The research finished. Document building is not set up yet, so the findings are shown here.",
  budget_cap: "The run reached its approved spend limit and stopped there.",
  slice_ceiling: "The run took longer than its time allowance and stopped.",
  canceled_by_user: "Someone in your workspace canceled this run.",
  approval_timeout: "The spend approval was not given in time, so the run was canceled.",
  gate_failed: "The final quality check found problems, so nothing was released. The problems are listed below; fix them and resume.",
  gate_not_configured: "The final quality check is not set up, so nothing was released. Everything collected is kept.",
  no_documents: "The run had no documents to read.",
  documents_not_ready: "Some documents were not ready when the run started. Wait until they show Indexed, then start again.",
  model_data_class_refused: "No language model is approved for private documents yet, so the files could not be read in full.",
  model_unavailable: "The language model was unavailable. Everything collected before that is kept.",
};

// quality check names, in plain words (codes and identifiers only ever reach this screen)
export const GATE_CHECKS: Record<string, string> = {
  tracker_rows: "A research area has no status or no reason",
  fact_citations: "A finding has no link or file citation",
  client_dashes: "A client file uses a dash as punctuation",
  client_terms: "A client file names a data source or a credit count",
  leak_check: "A client file repeats your private uploaded material",
  internal_label: "The Internal Only file is missing or unlabelled",
};

const GENERIC_STOP = "The run stopped before it finished. Everything it had collected is kept.";

/** The sentence for a stop reason code (null when there is no reason). */
export function stopExplanation(reason: string | null | undefined): string | null {
  if (!reason) return null;
  const code = reason.startsWith("build_failed:") ? "build_failed" : reason;
  return EXPLANATIONS[code] ?? GENERIC_STOP;
}

/** Total items a run holds (paid pulls, reused pulls, facts, drafts, files). */
export function storedTotal(stored: StoredCounts | null | undefined): number {
  if (!stored) return 0;
  return stored.paid_calls + stored.cached_calls + stored.facts + stored.drafts + stored.deliverables;
}

/** A finished run that stopped short and holds something can be resumed: the new run reuses
 * every finished pull and model reply of this one at no cost and pays only for what is missing.
 * A stop only a person can fix (a refused key) is still offered; the explanation says what to fix. */
export function canResume(status: string, stored: StoredCounts | null | undefined): boolean {
  const s = status.toUpperCase();
  if (!TERMINAL.has(s) || s === "COMPLETED") return false;
  return storedTotal(stored) > 0;
}
