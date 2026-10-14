"use client";
// The account's own composer: "Ask about <account>", scoped to that account with the web off.
// Asking opens Ask with the account preselected and the question sent.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AskComposer, type ScopeAccount } from "@/components/ask/AskComposer";

export function AccountAsk({ account }: { account: ScopeAccount }) {
  const router = useRouter();
  const [draft, setDraft] = useState("");
  const [hint, setHint] = useState(false);
  const send = () => {
    const q = draft.trim();
    if (q) router.push(`/?account=${encodeURIComponent(account.domain)}&q=${encodeURIComponent(q)}`);
  };
  return (
    <AskComposer
      variant="account"
      placeholder={`Ask anything about ${account.name}`}
      draft={draft}
      onDraft={setDraft}
      onSend={send}
      busy={false}
      accounts={[account]}
      scope={account}
      webHintOpen={hint}
      onWebHint={setHint}
    />
  );
}
