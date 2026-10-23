// All chats: the full list (the sidebar shows the six most recent), each with Open and Delete.
import Link from "next/link";
import type { Metadata } from "next";
import type { ChatThreads } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { StateCard } from "@/ui/design";
import { ChatList } from "@/components/ask/ChatList";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Chats · Account Intelligence" };

export default async function ChatsPage() {
  let threads: ChatThreads | null = null;
  try {
    threads = await serverGet<ChatThreads>("/v1/chat/threads", "/chats");
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
  }
  const sorted = (threads?.threads ?? []).slice().sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  return (
    <div className="pg center">
      <div className="col w760">
        <div>
          <h1>Chats</h1>
          <p className="intro">Every question you have asked, newest first. Open a chat to carry on, or delete the ones you no longer need.</p>
        </div>
        {threads ? <ChatList threads={sorted} /> : (
          <StateCard title="Your chats are unavailable right now" body="Reload the page in a moment." action={<Link className="btn" href="/chats" prefetch={false}>Reload</Link>} />
        )}
      </div>
    </div>
  );
}
