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
}): TurnUsage {
  const cost = Number(t.cost_usd);
  return {
    input: count(t.input_tokens),
    output: count(t.output_tokens),
    cacheRead: count(t.cache_read_tokens),
    cacheWrite: count(t.cache_write_tokens),
    costUsd: Number.isFinite(cost) && cost > 0 ? cost : 0,
    model: t.model_id || null,
  };
}

/** The whole context the model read for an answer: uncached, cached and newly cached input. */
export function contextTokens(u: TurnUsage): number {
  return u.input + u.cacheRead + u.cacheWrite;
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
