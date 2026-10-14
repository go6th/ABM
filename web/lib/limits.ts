// Per-user limits in plain words. Pure; the hook and the request form live in components/Limits.tsx.
import { ApiError } from "@/lib/api";
import type { MyLimits } from "@/lib/gen/api.gen";

export type LimitKind = "researches" | "chats";

const NOUN: Record<LimitKind, [string, string]> = { researches: ["research", "researches"], chats: ["chat", "chats"] };

/** "1 of 2 researches left this month" (or "left", when limits never reset). */
export function limitLine(limits: MyLimits, kind: LimitKind): string {
  const use = limits[kind];
  const noun = NOUN[kind][use.limit === 1 ? 0 : 1];
  return `${use.left} of ${use.limit} ${noun} left${limits.reset === "monthly" ? " this month" : ""}`;
}

/** The refusal of a used-up limit, from the API's ``limit_reached`` problem. */
export function limitReached(cause: unknown): { kind: LimitKind; message: string } | null {
  if (!(cause instanceof ApiError) || cause.type !== "limit_reached") return null;
  const kind = cause.context?.limit === "chats" ? "chats" : "researches";
  return { kind, message: cause.detail };
}
