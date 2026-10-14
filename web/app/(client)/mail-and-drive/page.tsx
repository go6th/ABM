// Mail and drive (screen 8): the signed-in user's own read-only connections.
import type { Metadata } from "next";
import type { MailAndDrive } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { MailDrive } from "@/components/accounts/MailDrive";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Mail and drive · Account Intelligence" };

export default async function MailAndDrivePage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const failedConsent = (await searchParams).error === "consent";
  let view: MailAndDrive | null = null;
  try {
    view = await serverGet<MailAndDrive>("/v1/mail-and-drive", "/mail-and-drive");
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
  }
  return (
    <div className="pg center">
      <div className="col w760">
        <div>
          <h1>Mail and drive</h1>
          <p className="intro">
            Connect your own mail and files so answers can include what you already know about an account. Access is read only, and only items that match your accounts are used.
          </p>
        </div>
        {failedConsent ? <p className="sm-text bad-text" role="alert">The connection was not completed. Try again.</p> : null}
        {view === null ? (
          <StateCard title="Mail and drive is unavailable right now" body="Reload the page in a moment." />
        ) : !view.allowed ? (
          <StateCard title="Mail and drive is turned off" body="Your administrator has turned off mail and drive connections for your workspace." />
        ) : (
          <MailDrive view={view} />
        )}
      </div>
    </div>
  );
}
