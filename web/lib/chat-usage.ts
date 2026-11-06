// The tokens and cost one Ask answer used, as the provider reported them: from the "done" frame of
// the Ask stream when the answer arrives, and from the stored turn when a thread is reopened.
// Pure helpers; the meter is components/ask/ContextMeter.tsx.

export interface TurnUsage {
  /** input tokens that were not served from the prompt cache */
  input: number;
  output: number;
  /** input tokens served from the prompt cache */
  cacheRead: number;
  /** input tokens written to the prompt cache */
  cacheWrite: number;
  costUsd: number;
  /** the model that wrote the answer; null for a workspace answer */
  model: string | null;
  /** the LAST tool round's whole prompt: how full the window is (the counts above add every
   * round); null for a turn stored before 2026-11-04 */
  context?: number | null;
}

function count(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0;
}

/** The "done" frame of a new answer; a frame without token counts (a replayed answer) gives none. */
export function usageFromDone(data: Record<string, unknown>): TurnUsage | undefined {
  if (!("input_tokens" in data)) return undefined;
  const cost = Number(data.cost_usd);
  return {
    input: count(data.input_tokens),
    output: count(data.output_tokens),
    cacheRead: count(data.cache_read_tokens),
    cacheWrite: count(data.cache_write_tokens),
    costUsd: Number.isFinite(cost) && cost > 0 ? cost : 0,
    model: typeof data.model === "string" ? data.model : null,
    context: count(data.context_tokens) || null,
  };
}

/** A stored turn of a thread. */
export function usageFromTurn(t: {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_usd: string;
  model_id: string;
  answer?: unknown;
}): TurnUsage {
  const cost = Number(t.cost_usd);
  const stored =
    t.answer && typeof t.answer === "object"
      ? (t.answer as Record<string, unknown>).context_tokens
      : undefined;
  return {
    input: count(t.input_tokens),
    output: count(t.output_tokens),
    cacheRead: count(t.cache_read_tokens),
    cacheWrite: count(t.cache_write_tokens),
    costUsd: Number.isFinite(cost) && cost > 0 ? cost : 0,
    model: t.model_id || null,
    context: count(stored) || null,
  };
}

/** Everything the model read for an answer, every tool round added: uncached, cached and newly
 * cached input. What the turn paid for, not how full the window is (see contextTokens). */
export function readTokens(u: TurnUsage): number {
  return u.input + u.cacheRead + u.cacheWrite;
}

/** How full the context window is: the last round's prompt, or null when the turn did not
 * report it (an older stored turn). The sum of every round is not a window share: 808K read
 * over ten rounds showed "81%" of a 1M window (staging 2026-11-03). */
export function contextTokens(u: TurnUsage): number | null {
  return u.context ?? null;
}

/** Every answer of a conversation added together (the model is not summed). */
export function sumUsage(list: TurnUsage[]): TurnUsage {
  return list.reduce<TurnUsage>(
    (total, u) => ({
      input: total.input + u.input,
      output: total.output + u.output,
      cacheRead: total.cacheRead + u.cacheRead,
      cacheWrite: total.cacheWrite + u.cacheWrite,
      costUsd: total.costUsd + u.costUsd,
      model: null,
    }),
    { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, costUsd: 0, model: null },
  );
}
