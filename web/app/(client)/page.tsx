// Ask (home). ?thread opens a recent chat, ?account preselects an account (by domain) and ?q asks
// that question on arrival (the account page's composer sends people here).
import type { Metadata } from "next";
import type { AccountPage, ChatSuggestions, ChatThread } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { Ask, type AskTurn } from "@/components/ask/Ask";
import { StateCard } from "@/ui/design";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Ask · Account Intelligence" };

export default async function Home({ searchParams }: { searchParams: Promise<{ thread?: string; account?: string; q?: string }> }) {
  const { thread, account, q } = await searchParams;
  let accounts: AccountPage | null = null;
  let suggestions: ChatSuggestions | null = null;
  let opened: ChatThread | null = null;
  let failed = false;
  try {
    [accounts, suggestions, opened] = await Promise.all([
      serverGet<AccountPage>("/v1/accounts?limit=200"),
      serverGet<ChatSuggestions>("/v1/chat/suggestions"),
      thread ? serverGet<ChatThread>(`/v1/chat/threads/${encodeURIComponent(thread)}`, "/") : Promise.resolve(null),
    ]);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    failed = true;
  }
  if (failed) {
    return (
      <div className="pg">
        <StateCard title="Ask is unavailable right now" body="Your workspace could not be reached. Reload the page in a moment." action={<a className="btn" href="/">Reload</a>} />
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
    error: t.answer ? undefined : "This answer was not kept. Ask it again.",
  }));
  const scope = account ? list.find((a) => a.domain === account) ?? null : null;
  return (
    <Ask
      accounts={list}
      suggestions={suggestions?.suggestions ?? []}
      threadId={opened?.id ?? null}
      initialTurns={turns}
      initialScope={scope}
      initialQuestion={q && q.trim() ? q.trim().slice(0, 4000) : null}
    />
  );
}
