// Ask (home): the conversation, scoped to all accounts or one account; ?thread opens a recent
// chat and ?account preselects an account.
import { Suspense } from "react";
import type { AccountPage, ChatThread } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { Ask, type Turn } from "@/components/ws/Ask";
import { LoadError } from "@/ui/primitives";

export const dynamic = "force-dynamic";

export default async function Home({ searchParams }: { searchParams: Promise<{ thread?: string; account?: string }> }) {
  const { thread, account } = await searchParams;
  let accounts: AccountPage | null = null;
  let opened: ChatThread | null = null;
  let reference: string | null | undefined;
  try {
    accounts = await serverGet<AccountPage>("/v1/accounts?limit=200");
    if (thread) opened = await serverGet<ChatThread>(`/v1/chat/threads/${encodeURIComponent(thread)}`);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    reference = e.reference;
  }
  if (reference !== undefined) return <div className="page narrow"><LoadError reference={reference} retryHref="/" /></div>;
  const list = (accounts?.accounts ?? []).map((a) => ({ domain: a.domain, name: a.name ?? a.domain }));
  const turns: Turn[] = (opened?.turns ?? [])
    .filter((t) => t.status !== "failed")
    .map((t) => ({ id: t.id, question: t.user_content, answer: t.assistant_content ?? "", status: "done" as const, scope: null }));
  const scope = account && list.some((a) => a.domain === account) ? account : null;
  return (
    <Suspense>
      <Ask key={opened?.id ?? "new"} accounts={list} threadId={opened?.id ?? null} initialTurns={turns} initialScope={scope} />
    </Suspense>
  );
}
