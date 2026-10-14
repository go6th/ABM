// Analysis of your file (screen 10) for one account: the given file run (?run=), else the
// account's latest one. Without any file analysis the screen says so and offers the upload.
import type { Metadata } from "next";
import Link from "next/link";
import type { FileAnalysisRun } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { FileAnalysis } from "@/components/ws/FileAnalysis";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "File analysis · Account Intelligence" };

export default async function FileAnalysisPage({ params, searchParams }: { params: Promise<{ domain: string }>; searchParams: Promise<{ run?: string }> }) {
  const domain = decodeURIComponent((await params).domain);
  const run = (await searchParams).run;
  const query = run && /^[0-9a-f-]{36}$/.test(run) ? `?run=${run}` : "";
  const here = `/accounts/${encodeURIComponent(domain)}/files${query}`;
  let found: FileAnalysisRun | null = null;
  let failed = false;
  try {
    found = await serverGet<FileAnalysisRun>(`/v1/accounts/${encodeURIComponent(domain)}/file-analysis${query}`, here);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = e.status !== 404;
  }
  return (
    <div className="pg tight">
      {found ? (
        <FileAnalysis domain={domain} run={found} />
      ) : (
        <div className="col w680 stack-22">
          <Link className="back" href={`/accounts/${encodeURIComponent(domain)}`} prefetch={false}>‹ Back to the account</Link>
          <h1 className="sr-only">File analysis</h1>
          {failed ? (
            <StateCard title="The analysis is unavailable right now" body="Reload the page in a moment." />
          ) : (
            <StateCard
              title="No file analysis yet"
              body="Upload account notes, a plan or an export and get an analysis from the file alone."
              action={<Link className="btn" href="/research/new?tab=file">Upload a file</Link>}
            />
          )}
        </div>
      )}
    </div>
  );
}
