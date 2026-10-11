"use client";
// Home's question box, scoped to all accounts. With no accounts yet it offers three starting
// questions and Add account instead of an empty page.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Conversation } from "./Conversation";
import { m } from "@/lib/messages";

export function HomeAsk({ hasAccounts }: { hasAccounts: boolean }) {
  const router = useRouter();
  return (
    <Conversation
      placeholder={m.composer.allAccounts}
      largeComposer
      onThread={() => router.refresh()}
      suggestions={hasAccounts ? undefined : m.home.suggestions}
      empty={hasAccounts ? undefined : (
        <div className="ws-empty home-empty">
          <h3>{m.home.noAccountsTitle}</h3>
          <p>{m.home.noAccountsBody}</p>
          <div className="actions"><Link className="ws-btn primary" href="/accounts#add">{m.nav.add}</Link></div>
        </div>
      )}
    />
  );
}
