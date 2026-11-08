// Ask. ?thread opens a chat, ?account preselects an account (by domain) and ?q asks that question
// on arrival (the account page's composer sends people here). Ask left the sidebar on 2026-11-08
// (owner order), so a plain visit to the home address opens Accounts instead.
import Link from "next/link";
import { redirect } from "next/navigation";
import type { Metadata } from "next";
import type { AccountPage, ChatThread, Me } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { Ask, type AskTurn } from "@/components/ask/Ask";
import { usageFromTurn } from "@/lib/chat-usage";
import { StateCard } from "@/ui/design";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Ask · Account Intelligence" };

export default async function Home({ searchParams }: { searchParams: Promise<{ thread?: string; account?: string; q?: string }> }) {
  const { thread, account, q } = await searchParams;
  if (!thread && !account && !q) redirect("/accounts");
  let accounts: AccountPage | null = null;
  let opened: ChatThread | null = null;
  let me: Me | null = null;
  let failed = false;
  try {
    [accounts, opened, me] = await Promise.all([
      serverGet<AccountPage>("/v1/accounts?limit=200"),
      thread ? serverGet<ChatThread>(`/v1/chat/threads/${encodeURIComponent(thread)}`, "/") : Promise.resolve(null),
      serverGet<Me>("/v1/me").catch(() => null), // the greeting only: never blocks Ask
    ]);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  if (failed) {
    return (
      <div className="pg">
        <StateCard title="Ask is unavailable right now" body="Your workspace could not be reached. Reload the page in a moment." action={<Link className="btn" href="/" prefetch={false}>Reload</Link>} />
      </div>
    );
  }
  const list = (accounts?.accounts ?? [])
    .filter((a) => a.id)
    .map((a) => ({ id: a.id as string, domain: a.domain, name: a.name ?? a.domain }))
    .sort((x, y) => x.name.localeCompare(y.name));
  const turns: AskTurn[] = (opened?.turns ?? []).map((t) => ({
    key: t.request_id,
    question: t.user_content,
    status: t.answer ? "done" : "failed",
    answer: t.answer ?? undefined,
    usage: t.answer ? usageFromTurn(t) : undefined,
    error: t.answer ? undefined : "This answer was not kept. Ask it again.",
  }));
  const scope = account ? list.find((a) => a.domain === account) ?? null : null;
  return (
    <Ask
      accounts={list}
      threadId={opened?.id ?? null}
      initialTurns={turns}
      initialScope={scope}
      initialQuestion={q && q.trim() ? q.trim().slice(0, 4000) : null}
      userName={me?.display_name ?? null}
    />
  );
}
