// The account's archive, shown like a web archive. With ?url= it is the archived copy of that
// address (what a closed job posting said, with its original link beside it); without, the index
// of everything archived for the account, newest capture first, with Show more. Both read the
// archive routes of the API through the server helper, so the session never reaches the browser.
import type { Metadata } from "next";
import Link from "next/link";
import type { ArchiveList, ArchivedCopy } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { ArchivedCopyView, ArchiveIndexView } from "@/components/accounts/ArchiveView";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Archive · Account Intelligence" };

const PAGE = 50;
const MAX_LIMIT = 200;
// profile copies are internal (the API never lists or serves them), so there is no profile filter
const KINDS = new Set(["posting", "page", "feed_item", "detection"]);

type Props = { params: Promise<{ domain: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> };

function segment(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export default async function ArchivePage({ params, searchParams }: Props) {
  const domain = segment((await params).domain);
  const query = await searchParams;
  const here = `/accounts/${encodeURIComponent(domain)}`;
  const base = `${here}/archive`;
  const url = one(query.url);

  if (url) {
    const returnTo = `${base}?${new URLSearchParams({ url }).toString()}`;
    let copy: ArchivedCopy | null = null;
    let failed = false;
    try {
      copy = await serverGet<ArchivedCopy>(`/v1/accounts/${encodeURIComponent(domain)}/archive?${new URLSearchParams({ url }).toString()}`, returnTo);
    } catch (e) {
      if (!(e instanceof ServerApiError)) throw e;
      failed = true;
    }
    if (!copy) {
      return (
        <div className="pg tight gap-18">
          <Link className="back" href={base} prefetch={false}>‹ Back to the archive</Link>
          {failed
            ? <StateCard title="This copy could not be loaded" body="Reload the page in a moment." action={<a className="btn" href={returnTo}>Reload</a>} />
            : <StateCard title="No archived copy" body="Nothing was stored for that address in this account." action={<Link className="btn" href={base}>See what is archived</Link>} />}
        </div>
      );
    }
    return <ArchivedCopyView copy={copy} domain={domain} />;
  }

  const kind = one(query.kind) && KINDS.has(one(query.kind)!) ? one(query.kind)! : "";
  const limit = Math.min(MAX_LIMIT, Math.max(PAGE, Math.floor(Number(one(query.limit))) || PAGE));
  const offset = Math.max(0, Math.floor(Number(one(query.offset)) || 0));
  const q = new URLSearchParams({ limit: String(limit) });
  if (offset) q.set("offset", String(offset));
  if (kind) q.set("kind", kind);
  const returnTo = `${base}${kind ? `?kind=${kind}` : ""}`;
  let list: ArchiveList | null = null;
  let failed = false;
  try {
    list = await serverGet<ArchiveList>(`/v1/accounts/${encodeURIComponent(domain)}/archive/list?${q.toString()}`, returnTo);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  if (!list) {
    return (
      <div className="pg tight gap-18">
        <Link className="back" href={here} prefetch={false}>‹ Back to the account</Link>
        {failed
          ? <StateCard title="The archive could not be loaded" body="Reload the page in a moment." action={<a className="btn" href={returnTo}>Reload</a>} />
          : <StateCard title="Account not found" body="It is not in your workspace." action={<Link className="btn" href="/accounts">Back to accounts</Link>} />}
      </div>
    );
  }
  // Show more grows the page up to 200 rows, then moves on by that many
  const shown = (list.offset ?? offset) + (list.items ?? []).length;
  const grow = (o: number, l: number) => `${base}?${new URLSearchParams({ ...(kind ? { kind } : {}), limit: String(l), ...(o ? { offset: String(o) } : {}) }).toString()}`;
  const showMore = shown < list.total ? (limit < MAX_LIMIT && offset === 0 ? grow(0, Math.min(MAX_LIMIT, limit + PAGE)) : grow(offset + limit, limit)) : null;
  return <ArchiveIndexView list={list} domain={domain} name={domain} kind={kind} showMore={showMore} />;
}
