import { redirect } from "next/navigation";
import type { ChatThreads, Me, Notifications } from "@/lib/gen/api.gen";
import { shellNotes } from "@/lib/notifications";
import { serverGet } from "@/lib/server-api";
import { WorkspaceShell } from "@/components/ws/WorkspaceShell";

export const dynamic = "force-dynamic";

export default async function ClientLayout({ children }: { children: React.ReactNode }) {
  const me = await serverGet<Me>("/v1/me");
  if (!me) redirect("/login");
  // both optional, independent of each other: read together, and a failure of either is shown as none
  const [threadsRead, notificationsRead] = await Promise.allSettled([
    serverGet<ChatThreads>("/v1/chat/threads"),
    serverGet<Notifications>("/v1/notifications"),
  ]);
  const threads = threadsRead.status === "fulfilled" ? threadsRead.value : null;
  const notifications = notificationsRead.status === "fulfilled" ? notificationsRead.value : null;
  const notes = shellNotes(notifications);
  const recent = (threads?.threads ?? [])
    .slice()
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 6)
    .map((t) => ({ id: t.id, title: t.title }));
  return (
    <WorkspaceShell
      user={{
        userId: me.user_id,
        clientName: me.tenant_name,
        userName: me.display_name,
        email: me.email,
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
