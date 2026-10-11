import { redirect } from "next/navigation";
import { AccountPage, Me } from "@/lib/gen/api.gen";
import { serverGet } from "@/lib/server-api";
import { WorkspaceShell } from "@/components/ws/WorkspaceShell";
import { currentTheme } from "@/ui/theme-server";

export const dynamic = "force-dynamic";

export default async function ClientLayout({ children }: { children: React.ReactNode }) {
  const me = await serverGet<Me>("/v1/me");
  if (!me) redirect("/login");
  const accounts = await serverGet<AccountPage>("/v1/accounts?limit=200");
  const limit = me.plan_limits.accounts ?? 0;
  return (
    <WorkspaceShell
      clientName={me.tenant_name}
      userName={me.display_name}
      email={me.email}
      used={accounts?.accounts.length ?? 0}
      limit={limit}
      theme={await currentTheme()}
    >
      {children}
    </WorkspaceShell>
  );
}
