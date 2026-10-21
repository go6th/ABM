// What a research is expected to use, in plain words (owner decision 2026-10-21: "this much will
// be used, should I proceed"). Pure; the Proceed / Cancel panel is components/UsageProceed.tsx.
import type { UsageEstimate } from "@/lib/gen/api.gen";

/** 2500000 -> "2.5M", 300000 -> "300K", 950 -> "950". */
export function tokenCount(n: number): string {
  const short = (v: number) => (v >= 10 ? Math.round(v).toString() : (Math.round(v * 10) / 10).toString());
  if (n >= 1_000_000) return `${short(n / 1_000_000)}M`;
  if (n >= 1_000) return `${short(n / 1_000)}K`;
  return `${Math.round(n)}`;
}

/** "$11.00", or "under $0.01". */
export function dollars(usd: number): string {
  return usd < 0.01 ? "under $0.01" : `$${usd.toFixed(2)}`;
}

/** "About 2.5M tokens, about $11.00". */
export function usageLine(u: UsageEstimate): string {
  return `About ${tokenCount(u.tokens)} tokens, about ${dollars(u.cost_usd)}`;
}

/** Where the figure comes from: the workspace's own finished runs, or a rough default. */
export function usageBasis(u: UsageEstimate): string {
  if (u.runs === 0) return "A rough estimate: your workspace has no finished run of this kind yet.";
  return u.runs === 1
    ? "Based on your last finished run of this kind."
    : `Based on your last ${u.runs} finished runs of this kind.`;
}
