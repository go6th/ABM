// Mail and drive (screen 8): the signed-in user's own read-only connections. A consent that came
// back without a connection carries a fixed code (?error=denied|provider|scope|expired|consent);
// the banner turns it into a plain sentence with a link that dismisses it.
import type { Metadata } from "next";
import Link from "next/link";
import { m } from "@/lib/messages";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { MailDrive, type MailDriveView } from "@/components/accounts/MailDrive";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Mail and drive · Account Intelligence" };

const BANNER = m.mailDrive.banner;
type BannerCode = "denied" | "provider" | "scope" | "expired" | "consent";

/** The banner sentence for a callback error code; an unknown code reads as ``consent``. */
function bannerFor(code: string | undefined): string | null {
  if (!code) return null;
  const known: BannerCode[] = ["denied", "provider", "scope", "expired", "consent"];
  return BANNER[known.includes(code as BannerCode) ? (code as BannerCode) : "consent"];
}

export default async function MailAndDrivePage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const banner = bannerFor((await searchParams).error);
  let view: MailDriveView | null = null;
  try {
    view = await serverGet<MailDriveView>("/v1/mail-and-drive", "/mail-and-drive");
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
        {banner ? (
          <div className="notice warn" role="alert">
            <p>{banner}</p>
            <Link className="link-btn" href="/mail-and-drive" replace>{BANNER.dismiss}</Link>
          </div>
        ) : null}
        {view === null ? (
          <StateCard title="Mail and drive is unavailable right now" body="Reload the page in a moment." />
        ) : !view.allowed ? (
          <StateCard title="Mail and drive is turned off" body={m.mailDrive.turnedOff} />
        ) : (
          <MailDrive view={view} />
        )}
      </div>
    </div>
  );
}
