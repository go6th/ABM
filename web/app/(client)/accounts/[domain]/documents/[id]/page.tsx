// One uploaded document as the product read it (workspace import phase 1, 2026-11-03): its
// status, the files inside a zip (each with its role, whether it was read and why not), and
// every passage in order. Read on the server from the document's status, members and passages;
// a plain file has no members (404 or an empty list) and shows one section.
import type { Metadata } from "next";
import Link from "next/link";
import type { DocumentMembers, DocumentPassage, DocumentPassages, DocumentStatus } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { DocumentView } from "@/components/accounts/DocumentView";

export const dynamic = "force-dynamic";

const PAGE = 200;
// 50 pages of 200: a document read into more passages than that shows its first 10,000
const MAX_PAGES = 50;

type Props = { params: Promise<{ domain: string; id: string }> };

/** A path segment as text: a malformed escape ("%zz") is kept as typed instead of crashing. */
function segment(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export async function generateMetadata(): Promise<Metadata> {
  return { title: "Uploaded file · Account Intelligence" };
}

/** Every passage, in seq order, paging through next_after. */
async function allPassages(api: string, returnTo: string): Promise<{ passages: DocumentPassage[]; truncated: boolean }> {
  const passages: DocumentPassage[] = [];
  let after = -1;
  // LOOP(passages): cap=MAX_PAGES pages of PAGE; exit=next_after null or an empty page
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const reply = await serverGet<DocumentPassages>(`${api}/passages?after=${after}&limit=${PAGE}`, returnTo);
    if (!reply || !reply.passages.length) return { passages, truncated: false };
    passages.push(...reply.passages);
    if (reply.next_after === null || reply.next_after === undefined || reply.next_after === after) return { passages, truncated: false };
    after = reply.next_after;
  }
  return { passages, truncated: true };
}

function Failed({ back, here, failed }: { back: string; here: string; failed: boolean }) {
  return (
    <div className="pg tight gap-18">
      <Link className="back" href={back} prefetch={false}>‹ Files</Link>
      {failed
        ? <StateCard title="This file could not be loaded" body="Reload the page in a moment." action={<a className="btn" href={here}>Reload</a>} />
        : <StateCard title="File not found" body="It is not in your workspace." action={<Link className="btn" href={back}>Back to the account</Link>} />}
    </div>
  );
}

export default async function DocumentPage({ params }: Props) {
  const { domain: rawDomain, id: rawId } = await params;
  const domain = segment(rawDomain);
  const id = segment(rawId);
  const back = `/accounts/${encodeURIComponent(domain)}/files`;
  const here = `/accounts/${encodeURIComponent(domain)}/documents/${encodeURIComponent(id)}`;
  const api = `/v1/documents/${encodeURIComponent(id)}`;
  let doc: DocumentStatus | null = null;
  let members: DocumentMembers | null = null;
  let read: { passages: DocumentPassage[]; truncated: boolean } = { passages: [], truncated: false };
  let failed = false;
  try {
    doc = await serverGet<DocumentStatus>(`${api}/status`, here);
    if (doc) {
      // a plain file has no members: a 404 or an empty list both mean one section
      [members, read] = await Promise.all([serverGet<DocumentMembers>(`${api}/members`, here), allPassages(api, here)]);
    }
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  if (failed || !doc) return <Failed back={back} here={here} failed={failed} />;
  return (
    <div className="pg tight gap-18">
      <Link className="back" href={back} prefetch={false}>‹ Files</Link>
      <DocumentView doc={doc} members={members?.members ?? []} passages={read.passages} />
      {read.truncated ? (
        <div className="fine">The first {read.passages.length.toLocaleString()} sections are shown. The rest were read but are not listed here.</div>
      ) : null}
    </div>
  );
}
