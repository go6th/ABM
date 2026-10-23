// Teammates (client admins): invite people by email and role, and see who is in the workspace.
import type { Metadata } from "next";
import type { Team } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { Teammates } from "@/components/accounts/Teammates";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Teammates · Account Intelligence" };

export default async function TeamPage() {
  let team: Team | null = null;
  let forbidden = false;
  try {
    team = await serverGet<Team>("/v1/users/team", "/team");
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    forbidden = e.status === 403;
  }
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
      </div>
    </div>
  );
}
