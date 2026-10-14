// Add an account (screen 6). A static route, so it is never caught by /accounts/[domain]. The
// research estimate and the library offer are read first; nothing is reserved or copied until
// the user confirms.
import type { Metadata } from "next";
import Link from "next/link";
import type { LibraryAvailable, ResearchEstimate } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { AddAccount } from "@/components/accounts/AddAccount";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Add account · Account Intelligence" };

async function optional<T>(path: string): Promise<T | null> {
  try {
    return await serverGet<T>(path, "/accounts/add");
  } catch (e) {
    if (e instanceof ServerApiError) return null;
    throw e;
  }
}

export default async function AddAccountPage() {
  const [estimate, library] = await Promise.all([
    optional<ResearchEstimate>("/v1/research/estimate"),
    optional<LibraryAvailable>("/v1/library/available"),
  ]);
  return (
    <div className="pg">
      <div className="col w820 stack-22">
        <div>
          <Link className="back" href="/accounts" prefetch={false}>‹ Accounts</Link>
          <h1 className="mt-4">Add an account</h1>
          <p className="intro">Three ways to bring an account into your workspace.</p>
        </div>
        <AddAccount estimate={estimate} library={library} />
      </div>
    </div>
  );
}
