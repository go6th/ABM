// Schedules: how often each account is refreshed from the web, within the client's credit pool.
import type { Metadata } from "next";
import type { Schedules } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { SchedulesView } from "@/components/accounts/SchedulesView";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Schedules · Account Intelligence" };

export default async function SchedulesPage({ searchParams }: { searchParams: Promise<{ account?: string }> }) {
  const { account } = await searchParams;
  let data: Schedules | null = null;
  let failed = false;
  try {
    data = await serverGet<Schedules>("/v1/schedules", "/schedules");
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  return (
    <div className="pg">
      <div>
        <h1>Schedules</h1>
        <p className="intro">Scheduled refreshes go to the web on your behalf, within your credit pool. Between refreshes, everything stays local.</p>
      </div>
      {failed || !data ? (
        <StateCard title="Schedules could not be loaded" body="Reload the page in a moment." action={<a className="btn" href="/schedules">Reload</a>} />
      ) : (
        <SchedulesView initial={data} focus={account ?? null} />
      )}
    </div>
  );
}
