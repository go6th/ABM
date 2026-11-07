// New research now lives on the Research page (redesign 2026-11-07); the old address still works
// and keeps the tab it asked for (company, or file with ?source=drive).
import { redirect } from "next/navigation";

export default async function Moved({ searchParams }: { searchParams: Promise<{ tab?: string; mode?: string; source?: string }> }) {
  const q = await searchParams;
  const file = q.tab === "file" || q.mode === "file";
  const next = new URLSearchParams({ ...(file ? { tab: "file" } : {}), ...(file && q.source === "drive" ? { source: "drive" } : {}) }).toString();
  redirect(next ? `/research?${next}` : "/research");
}
