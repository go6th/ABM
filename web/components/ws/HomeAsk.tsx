"use client";
// Home's question box, scoped to all accounts. With no accounts yet it offers three starting
// questions and Add account instead of an empty page.
import { Building2 } from "lucide-react";
import { EmptyState } from "@/ui/primitives";
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
        <EmptyState title={m.home.noAccountsTitle} body={m.home.noAccountsBody} icon={<Building2 size={20} strokeWidth={1.8} />} action={{ href: "/accounts#add", label: m.nav.add }} />
      )}
    />
  );
}
