// Research (redesign 2026-11-08): every web research running now and finished recently, live from
// the server (owner request 2026-10-21), with "Start new research" as a pop-up. Accounts are added
// and files imported on Accounts, so the old "Upload a file" tab (?tab=file) now opens Import a
// file there. ?start=1 opens the start pop-up (the old New research address); ?r=<job id> shows
// that research's steps.
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import type { ResearchEstimate, ResearchLive } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { ResearchBoard } from "@/components/research/ResearchBoard";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Research · Account Intelligence" };

export default async function ResearchPage({ searchParams }: { searchParams: Promise<{ r?: string; tab?: string; start?: string }> }) {
  const query = await searchParams;
  if (query.tab === "file") redirect("/accounts/import");
  const selected = query.r && /^[0-9a-f-]{36}$/i.test(query.r) ? `?selected=${query.r}` : "";
  let initial: ResearchLive | null = null;
  let estimate: ResearchEstimate | null = null;
  const [live, est] = await Promise.allSettled([
    serverGet<ResearchLive>(`/v1/research/live${selected}`, "/research"),
    serverGet<ResearchEstimate>("/v1/research/estimate", "/research"),
  ]);
  for (const r of [live, est]) {
    if (r.status === "rejected" && !(r.reason instanceof ServerApiError)) throw r.reason;
  }
  if (live.status === "fulfilled") initial = live.value;
  if (est.status === "fulfilled") estimate = est.value;
  return (
    <div className="pg">
      <ResearchBoard initial={initial} estimate={estimate} startOpen={query.start === "1"} />
    </div>
  );
}
