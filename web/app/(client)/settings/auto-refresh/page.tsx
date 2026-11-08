// Auto refresh (Settings): how often each account is refreshed from the web; each scheduled
// refresh is one of the user's researches (owner decision 2026-10-14). Was /schedules. A scheduled
// refresh runs with the account owner's saved models (runs.lifecycle._run_models), so the models
// card on top shows them and opens the model pop-up (2026-11-08).
import type { Metadata } from "next";
import type { Schedules } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { SchedulesView } from "@/components/accounts/SchedulesView";
import { ModelSummary } from "@/components/ModelPicker";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Auto refresh · Settings · Account Intelligence" };

export default async function AutoRefreshPage({ searchParams }: { searchParams: Promise<{ account?: string }> }) {
  const { account } = await searchParams;
  let data: Schedules | null = null;
  let failed = false;
  try {
    data = await serverGet<Schedules>("/v1/schedules", "/settings/auto-refresh");
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  return (
    <>
      <header>
        <h2>Auto refresh</h2>
        <p className="intro">Scheduled refreshes go to the web on your behalf. Between refreshes, nothing is sent to the web.</p>
      </header>
      <ModelSummary
        title="Models for scheduled refreshes"
        intro="Each scheduled refresh runs with these models. Changing them here changes them everywhere you start research."
      />
      <h3 className="sched-title">Schedule per account</h3>
      {failed || !data ? (
        <StateCard title="Schedules could not be loaded" body="This is usually brief." action={<a className="btn" href="/settings/auto-refresh">Try again</a>} />
      ) : (
        <SchedulesView initial={data} focus={account ?? null} />
      )}
    </>
  );
}
