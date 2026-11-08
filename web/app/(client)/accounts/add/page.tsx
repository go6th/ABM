// Add an account (screen 6): research a new company. A static route, so it is never caught by
// /accounts/[domain]. The research estimate is read first; nothing is reserved until the user
// confirms. The ready-made research and file sections were removed on 2026-11-08 (owner order).
import type { Metadata } from "next";
import Link from "next/link";
import type { ResearchEstimate } from "@/lib/gen/api.gen";
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
  const estimate = await optional<ResearchEstimate>("/v1/research/estimate");
  return (
    <div className="pg">
      <div className="col w820 stack-22">
        <div>
          <Link className="back" href="/accounts" prefetch={false}>‹ Accounts</Link>
          <h1 className="mt-4">Add an account</h1>
          <p className="intro">Research a company to add it to your workspace. To bring in your own file, use <Link className="link" href="/accounts/import" prefetch={false}>Import a file</Link>.</p>
        </div>
        <AddAccount estimate={estimate} />
      </div>
    </div>
  );
}
