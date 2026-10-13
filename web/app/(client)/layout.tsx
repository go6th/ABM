import { redirect } from "next/navigation";
import type { ChatThreads, Me } from "@/lib/gen/api.gen";
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
  const recent = (threads?.threads ?? [])
    .slice()
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 8)
    .map((t) => ({ id: t.id, title: t.title }));
  return (
    <WorkspaceShell clientName={me.tenant_name} userName={me.display_name} email={me.email} recent={recent}>
      {children}
    </WorkspaceShell>
  );
}
