// Research (owner request 2026-10-21): every web research running now and finished recently on
// one page, with Start and Stop, live from the server. The first paint is the server's snapshot;
// the board then follows the live stream.
import type { Metadata } from "next";
import type { ResearchEstimate, ResearchLive } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { ResearchBoard } from "@/components/research/ResearchBoard";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Research · Account Intelligence" };

export default async function ResearchPage({ searchParams }: { searchParams: Promise<{ r?: string }> }) {
  const query = await searchParams;
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
      <ResearchBoard initial={initial} estimate={estimate} />
    </div>
  );
}
