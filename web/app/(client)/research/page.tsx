// Research (redesign 2026-11-07): start a research on a company, or analyze your own file, on the
// left; every web research running now and finished recently on the right, live from the server
// (owner request 2026-10-21). Was two pages, Research and New research; the old address
// redirects here with its tab.
import type { Metadata } from "next";
import type { ResearchEstimate, ResearchLive } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { ResearchBoard } from "@/components/research/ResearchBoard";
import { NewResearch } from "@/components/accounts/NewResearch";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Research · Account Intelligence" };

export default async function ResearchPage({ searchParams }: { searchParams: Promise<{ r?: string; tab?: string }> }) {
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
  const tab = query.tab === "file" ? "file" : "company";
  return (
    <div className="pg">
      <div>
        <h1>Research</h1>
        <p className="intro">Research a company from public sources, or upload your own file to analyze. You are told when it is ready.</p>
      </div>
      <div className="research-grid">
        <section className="research-start" aria-label="Start a research">
          <NewResearch estimate={estimate} initialTab={tab} />
        </section>
        <ResearchBoard initial={initial} estimate={estimate} embedded />
      </div>
    </div>
  );
}
