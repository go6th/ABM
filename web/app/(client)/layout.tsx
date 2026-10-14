import { redirect } from "next/navigation";
import type { ChatThreads, Me, Notifications } from "@/lib/gen/api.gen";
import { serverGet } from "@/lib/server-api";
import { WorkspaceShell } from "@/components/ws/WorkspaceShell";

export const dynamic = "force-dynamic";

export default async function ClientLayout({ children }: { children: React.ReactNode }) {
  const me = await serverGet<Me>("/v1/me");
  if (!me) redirect("/login");
  let threads: ChatThreads | null = null;
  try {
    threads = await serverGet<ChatThreads>("/v1/chat/threads");
  } catch {
    threads = null;
  }
  let notifications: Notifications | null = null;
  try {
    notifications = await serverGet<Notifications>("/v1/notifications");
  } catch {
    notifications = null;
  }
  const scope: Record<string, string> = { jobs: "job postings", people: "people and teams", full: "full refresh", topic: "topic" };
  const notes = (notifications?.notifications ?? [])
    .filter((n) => !n.read)
    .slice(0, 3)
    .map((n) => {
      const name = n.account_name ?? "An account";
      const what = n.scope && scope[n.scope] ? ` (${scope[n.scope]})` : "";
      const text = n.kind === "access_expiring" ? "Your access ends in 3 days"
        : n.kind === "research_failed" ? `${name}: research did not finish`
        : n.kind === "waiting_credit_limit" ? `${name}: waiting for credits`
        : n.kind === "research_finished" ? `${name}: research finished`
        : n.kind === "library_added" ? `${name}: added from the 6th Library`
        : `${name}: refresh finished${what}`;
      const meta = n.kind === "access_expiring" ? "Using the workspace keeps it open."
        : n.kind === "research_failed" ? (n.reason ?? "No extra credits were used.")
        : n.credits_used ? `${n.credits_used} credits used` : "Open the account";
      return {
        id: n.id,
        text,
        meta,
        href: n.kind === "access_expiring" ? "/" : n.account_domain ? `/accounts/${encodeURIComponent(n.account_domain)}` : "/accounts",
        warn: n.kind === "research_failed" || n.kind === "waiting_credit_limit" || n.kind === "access_expiring",
      };
    });
  const recent = (threads?.threads ?? [])
    .slice()
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 6)
    .map((t) => ({ id: t.id, title: t.title }));
  return (
    <WorkspaceShell
      user={{
        clientName: me.tenant_name,
        userName: me.display_name,
        email: me.email,
        accountsUsed: me.accounts_used ?? 0,
        accountCap: me.account_cap ?? 0,
        mailDriveAllowed: me.mail_drive_allowed ?? true,
        modelReady: me.model_ready ?? true,
        isAdmin: me.role === "admin",
      }}
      recent={recent}
      notes={notes}
    >
      {children}
    </WorkspaceShell>
  );
}
