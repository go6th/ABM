// Refresh <account> from the web? The scopes, their estimates and the credits left are read
// first; nothing is reserved or sent anywhere until the user confirms.
import type { Metadata } from "next";
import Link from "next/link";
import type { RefreshOptions } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { RefreshForm } from "@/components/accounts/RefreshForm";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Refresh from web · Account Intelligence" };

const SCOPE = { jobs: "Job postings only", people: "People and teams", full: "Full refresh", file: "File analysis", topic: "Research on a topic" } as Record<string, string>;

function ago(days: number | null): string {
  if (days === null) return "has not been researched yet";
  if (days === 0) return "was last refreshed today";
  if (days === 1) return "was last refreshed yesterday";
  return `was last refreshed ${days} days ago`;
}

export default async function RefreshPage({ params, searchParams }: { params: Promise<{ domain: string }>; searchParams: Promise<{ topic?: string }> }) {
  const domain = decodeURIComponent((await params).domain);
  const initialTopic = ((await searchParams).topic ?? "").slice(0, 300);
  const back = `/accounts/${encodeURIComponent(domain)}`;
  let view: RefreshOptions | null = null;
  let failed = false;
  try {
    view = await serverGet<RefreshOptions>(`/v1/accounts/${encodeURIComponent(domain)}/refresh`, `${back}/refresh`);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  if (!view) {
    return (
      <div className="pg pad-56 center">
        <div className="col w680">
          <Link className="back" href={back} prefetch={false}>‹ Back to the account</Link>
          {failed ? <StateCard title="Refresh is unavailable right now" body="Reload the page in a moment." action={<a className="btn" href={`${back}/refresh`}>Reload</a>} />
            : <StateCard title="Account not found" body="It is not in your workspace." action={<Link className="btn" href="/accounts">Back to accounts</Link>} />}
        </div>
      </div>
    );
  }
  return (
    <div className="pg pad-56 center">
      <div className="col w680">
        <div>
          <Link className="back" href={back} prefetch={false}>‹ {view.name}</Link>
          <h1 className="big refresh-title">Refresh {view.name} from the web?</h1>
          <p className="intro lg">
            Your workspace {ago(view.refreshed_days_ago)}. A web refresh looks for anything newer. Nothing leaves your workspace until you confirm.
          </p>
        </div>
        {!view.allowed ? (
          <StateCard
            title="Web refresh is turned off"
            body="Your administrator has turned off web refreshes for your workspace. Answers keep coming from what your workspace already holds."
            action={<Link className="btn" href={back}>Stay local</Link>}
          />
        ) : view.open_job ? (
          <StateCard
            title={view.open_job.status === "waiting_credit_limit" ? "A refresh is waiting for credits" : "A refresh is already running"}
            body={`${SCOPE[view.open_job.scope] ?? "A refresh"} for ${view.name} ${view.open_job.status === "waiting_credit_limit" ? "starts when your credit pool can cover it" : "is in progress"}. You will be notified when it finishes.`}
            action={<Link className="btn" href={back}>Back to {view.name}</Link>}
          />
        ) : (
          <RefreshForm view={view} initialTopic={initialTopic} />
        )}
      </div>
    </div>
  );
}
