// Support access (client admins): give one named 6th staff member time-boxed, read-only access
// to this workspace, see every grant and every support view, and revoke at any time.
import type { Metadata } from "next";
import type { SupportEvents, SupportGrants } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { SupportAccess } from "@/components/accounts/SupportAccess";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Support access · Account Intelligence" };

export default async function SupportAccessPage() {
  let grants: SupportGrants | null = null;
  let events: SupportEvents | null = null;
  let forbidden = false;
  try {
    grants = await serverGet<SupportGrants>("/v1/support/grants", "/support-access");
    events = await serverGet<SupportEvents>("/v1/support/events", "/support-access");
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    forbidden = e.status === 403;
  }
  return (
    <div className="pg center">
      <div className="col w760">
        <div>
          <h1>Support access</h1>
          <p className="intro">Let a named person at 6th see your workspace, read-only, for a limited time. They see accounts and counts, never your questions, answers or files. Every view is recorded below.</p>
        </div>
        {grants && events ? <SupportAccess grants={grants.grants} events={events.events} /> : (
          <StateCard
            title={forbidden ? "Only your administrator can give support access" : "Support access is unavailable right now"}
            body={forbidden ? "Ask an administrator of your workspace." : "Reload the page in a moment."}
          />
        )}
      </div>
    </div>
  );
}
