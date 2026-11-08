import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { Me, ModelChoices, Notifications } from "@/lib/gen/api.gen";
import { ModelChoicesProvider } from "@/lib/model-choices";
import { shellNotes } from "@/lib/notifications";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { WorkspaceShell } from "@/components/ws/WorkspaceShell";

export const dynamic = "force-dynamic";

export default async function ClientLayout({ children }: { children: React.ReactNode }) {
  // one round trip for everything the shell needs (2026-10-24: three sequential rounds before
  // made every page change wait on the API three times); the optional reads fail to "none".
  // The recent chats are no longer read: Ask left the sidebar (2026-11-08).
  // A signed-out visit comes back to the page it opened after sign in (proxy.ts sets the path).
  const here = (await headers()).get("x-return-to") ?? "/";
  const [meRead, notificationsRead, choicesRead] = await Promise.allSettled([
    serverGet<Me>("/v1/me", here),
    serverGet<Notifications>("/v1/notifications", here),
    serverGet<ModelChoices>("/v1/models/choices", here),
  ]);
  // a signed-out session redirects to sign in (that is thrown, so it is passed on); any other failure
  // of this one read (a server blip during a background refresh) must not take the page away:
  // the page renders inside a plain shell and the next read fills the real name in
  if (meRead.status === "rejected" && !(meRead.reason instanceof ServerApiError)) throw meRead.reason;
  const me = meRead.status === "fulfilled" ? meRead.value : undefined;
  if (me === null) redirect("/login");
  const choices = choicesRead.status === "fulfilled" ? choicesRead.value : null;
  const notifications = notificationsRead.status === "fulfilled" ? notificationsRead.value : null;
  const notes = shellNotes(notifications);
  return (
    <WorkspaceShell
      user={me ? {
        userId: me.user_id,
        clientName: me.tenant_name,
        userName: me.display_name,
        email: me.email,
        modelReady: me.model_ready ?? true,
        isAdmin: me.role === "admin",
      } : { clientName: "Your workspace", userName: null, email: null, modelReady: true, isAdmin: false }}
      notes={notes}
    >
      <ModelChoicesProvider initial={choices}>{children}</ModelChoicesProvider>
    </WorkspaceShell>
  );
}
