import { redirect } from "next/navigation";
import type { ChatThreads, Me, ModelChoices, Notifications } from "@/lib/gen/api.gen";
import { ModelChoicesProvider } from "@/lib/model-choices";
import { shellNotes } from "@/lib/notifications";
import { serverGet } from "@/lib/server-api";
import { WorkspaceShell } from "@/components/ws/WorkspaceShell";

export const dynamic = "force-dynamic";

export default async function ClientLayout({ children }: { children: React.ReactNode }) {
  // one round trip for everything the shell needs (2026-10-24: three sequential rounds before
  // made every page change wait on the API three times); the optional reads fail to "none"
  const [meRead, threadsRead, notificationsRead, choicesRead] = await Promise.allSettled([
    serverGet<Me>("/v1/me"),
    serverGet<ChatThreads>("/v1/chat/threads"),
    serverGet<Notifications>("/v1/notifications"),
    serverGet<ModelChoices>("/v1/models/choices"),
  ]);
  if (meRead.status === "rejected") throw meRead.reason;
  const me = meRead.value;
  if (!me) redirect("/login");
  const choices = choicesRead.status === "fulfilled" ? choicesRead.value : null;
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
      <ModelChoicesProvider initial={choices}>{children}</ModelChoicesProvider>
    </WorkspaceShell>
  );
}
