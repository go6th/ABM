// Import a file (2026-11-08): upload account notes, a plan or an export, say which company it is
// about, and analyze it. A static route beside Add account, so it is never caught by
// /accounts/[domain]. It lived on the Research page as its "Upload a file" tab; the owner adds and
// imports accounts here on Accounts, so Research keeps only the research itself.
import type { Metadata } from "next";
import Link from "next/link";
import type { ResearchEstimate } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { ImportFile } from "@/components/accounts/ImportFile";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Import a file · Account Intelligence" };

export default async function ImportFilePage() {
  let estimate: ResearchEstimate | null = null;
  try {
    estimate = await serverGet<ResearchEstimate>("/v1/research/estimate", "/accounts/import");
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e; // the cost line is optional; the upload still works
  }
  return (
    <div className="pg">
      <div className="col w820 stack-22">
        <div>
          <Link className="back" href="/accounts" prefetch={false}>‹ Accounts</Link>
          <h1 className="mt-4">Import a file</h1>
          <p className="intro">Upload account notes, a plan or an export. Say which company it is about, and the file is added to that account and analyzed. Your files stay private to your workspace.</p>
        </div>
        <ImportFile estimate={estimate} />
      </div>
    </div>
  );
}
