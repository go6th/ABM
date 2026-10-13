// Refresh schedules per account. The API has no schedule operation yet, so the choice is kept in
// this browser until the scheduling service is connected; the account page says so. Read through
// useSyncExternalStore so the server render and the first client render agree.
import { useSyncExternalStore } from "react";

export type Cadence = "3d" | "weekly" | "2w" | "monthly" | "off";
export interface Schedule { cadence: Cadence; auto_continue: boolean }

const KEY = "ai.schedules";
const DEFAULT = "off|0";
const listeners = new Set<() => void>();

function readAll(): Record<string, Schedule> {
  try {
    const raw = typeof window !== "undefined" ? window.localStorage.getItem(KEY) : null;
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return typeof parsed === "object" && parsed !== null ? (parsed as Record<string, Schedule>) : {};
  } catch {
    return {};
  }
}

function snapshot(domain: string): string {
  const s = readAll()[domain];
  return s ? `${s.cadence}|${s.auto_continue ? 1 : 0}` : DEFAULT;
}

function parse(value: string): Schedule {
  const [cadence, auto] = value.split("|");
  return { cadence: (cadence as Cadence) || "off", auto_continue: auto === "1" };
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => { if (e.key === KEY) listener(); };
  window.addEventListener("storage", onStorage);
  return () => { listeners.delete(listener); window.removeEventListener("storage", onStorage); };
}

export function useSchedule(domain: string): Schedule {
  const value = useSyncExternalStore(subscribe, () => snapshot(domain), () => DEFAULT);
  return parse(value);
}

export function writeSchedule(domain: string, schedule: Schedule): void {
  try {
    const all = readAll();
    all[domain] = schedule;
    window.localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    // storage unavailable: the choice lasts for this page only
  }
  for (const l of listeners) l();
}
