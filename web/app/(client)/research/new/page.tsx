// New research (screen 9): two tabs. Company name researches a company from public sources on
// the credit pool (the estimate is shown and confirmed first). Upload a file analyzes only the
// files; public sources only when switched on, which is the only part that uses credits.
import type { Metadata } from "next";
import type { ResearchEstimate } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { NewResearch } from "@/components/accounts/NewResearch";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "New research · Account Intelligence" };

export default async function NewResearchPage({ searchParams }: { searchParams: Promise<{ tab?: string; mode?: string }> }) {
  const query = await searchParams;
  let estimate: ResearchEstimate | null = null;
  try {
    estimate = await serverGet<ResearchEstimate>("/v1/research/estimate", "/research/new");
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
  }
  const tab = query.tab === "file" || query.mode === "file" ? "file" : "company";
  return (
    <div className="pg pad-48 center">
      <div className="col w720">
        <div>
          <h1 className="big">New research</h1>
          <p className="intro">Start from a company name, or upload your own file and we will analyze what is in it.</p>
        </div>
        <NewResearch estimate={estimate} initialTab={tab} />
      </div>
    </div>
  );
}
