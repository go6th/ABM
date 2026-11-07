// The account's Files tab (redesign 2026-11-07): the research files to download, the delivered
// files read on screen, and your own uploads with their analysis. Was spread over the account
// page's right column and its middle; the analysis of your files keeps its own address
// (/file-analysis).
import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import type { AccountWorkspace, DocumentList, ParityScreen } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { FilesCard } from "@/components/accounts/FilesCard";
import { UploadedFilesCard } from "@/components/accounts/UploadedFilesCard";
import { LiveSync, RetryRefresh } from "@/components/accounts/LiveSync";
import { ParityWorkspace } from "@/components/accounts/ParityWorkspace";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ domain: string }> };

function segment(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  return { title: `Files · ${segment((await params).domain)} · Account Intelligence` };
}

/** The delivered files read on screen, when a built version exists (404 otherwise: nothing). */
async function Delivered({ domain, here }: { domain: string; here: string }) {
  let screen: ParityScreen | null = null;
  try {
    screen = await serverGet<ParityScreen>(`/v1/accounts/${encodeURIComponent(domain)}/parity/screen`, here, [404]);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    return null;
  }
  return screen ? <ParityWorkspace domain={domain} screen={screen} /> : null;
}

/** Your uploads for this account (viewers may not list documents: none shown), kept current while
 *  they are read. */
async function Uploads({ accountId, domain, here }: { accountId: string; domain: string; here: string }) {
  let uploads: DocumentList["documents"] | null = null;
  try {
    uploads = (await serverGet<DocumentList>(`/v1/documents?account_id=${encodeURIComponent(accountId)}`, here))?.documents ?? null;
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
  }
  return uploads ? (
    <>
      <UploadedFilesCard docs={uploads} domain={domain} />
      {uploads.length ? <Link className="link-strong sm-text" href={`/accounts/${encodeURIComponent(domain)}/file-analysis`} prefetch={false}>See the analysis of your files</Link> : null}
      <LiveSync accountId={accountId} docs={uploads} />
    </>
  ) : (
    <RetryRefresh />
  );
}

export default async function FilesPage({ params }: Params) {
  const domain = segment((await params).domain);
  const here = `/accounts/${encodeURIComponent(domain)}/files`;
  let ws: AccountWorkspace | null = null;
  let failed = false;
  try {
    ws = await serverGet<AccountWorkspace>(`/v1/accounts/${encodeURIComponent(domain)}/workspace`, here);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  if (failed || !ws) {
    return (
      <div className="pg">
        {failed
          ? <StateCard title="The files could not be loaded" body="Reload the page in a moment." action={<a className="btn" href={here}>Reload</a>} />
          : <StateCard title="Account not found" body="It is not in your workspace." action={<Link className="btn" href="/accounts">Back to accounts</Link>} />}
      </div>
    );
  }
  return (
    <div className="split">
      <div className="primary-col">
        <FilesCard domain={ws.domain} files={ws.files} />
        <Suspense fallback={null}><Delivered domain={ws.domain} here={here} /></Suspense>
      </div>
      <aside className="side-col stack-14" aria-label="Your uploaded files">
        {ws.id ? <Suspense fallback={null}><Uploads accountId={ws.id} domain={ws.domain} here={here} /></Suspense> : null}
        <p className="mut sm-text">Uploaded files stay in your workspace. They answer your questions and are never included in research files you share with a prospect.</p>
      </aside>
    </div>
  );
}
