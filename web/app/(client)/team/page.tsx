// Teammates (client admins): invite people by email and role, and see who is in the workspace.
import type { Metadata } from "next";
import type { PeopleConsent, Team } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { PeopleConsentCard } from "@/components/accounts/PeopleConsent";
import { Teammates } from "@/components/accounts/Teammates";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Teammates · Account Intelligence" };

export default async function TeamPage() {
  // the two reads do not depend on each other: asked together, the page waits for the slower
  // one instead of both. The people data consent (every member reads it; admins change it):
  // research runs build the senior people list only while it is granted
  const [teamRead, consentRead] = await Promise.allSettled([
    serverGet<Team>("/v1/users/team", "/team"),
    serverGet<PeopleConsent>("/v1/settings/people-consent", "/team"),
  ]);
  let team: Team | null = null;
  let forbidden = false;
  if (teamRead.status === "fulfilled") team = teamRead.value;
  else if (teamRead.reason instanceof ServerApiError) forbidden = teamRead.reason.status === 403;
  else throw teamRead.reason;
  let consent: PeopleConsent | null = null;
  if (consentRead.status === "fulfilled") consent = consentRead.value;
  else if (!(consentRead.reason instanceof ServerApiError)) throw consentRead.reason;
  return (
    <div className="pg center">
      <div className="col w760">
        <div>
          <h1>Teammates</h1>
          <p className="intro">Invite the people who work on your accounts. Everyone sees the same accounts and research; what each person can change depends on their role.</p>
        </div>
        {team ? <Teammates members={team.members} /> : (
          <StateCard
            title={forbidden ? "Only your administrator can manage teammates" : "Teammates are unavailable right now"}
            body={forbidden ? "Ask an administrator of your workspace to invite people." : "Reload the page in a moment."}
          />
        )}
        {consent ? <PeopleConsentCard consent={consent} canChange={team !== null} /> : null}
      </div>
    </div>
  );
}
