import { RUN_STATUS } from "@/lib/gen/labels.gen";

export function operatorStatus(value: string): string {
  const label = (RUN_STATUS as Record<string, string>)[value];
  return label ?? `Unknown (${value})`;
}

export function formatDate(value: string | null): string {
  if (value === null) return "Unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return "Unknown";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat("en", { maximumFractionDigits: 0 }).format(value);
}

export function formatMicros(value: number | null): string {
  if (value === null) return "Unknown";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 6 }).format(value / 1_000_000);
}

export function formatDuration(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "Not recorded";
  return `${new Intl.NumberFormat("en", { maximumFractionDigits: 2 }).format(value)} s`;
}
