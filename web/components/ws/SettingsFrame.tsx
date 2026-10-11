// The frame around every settings page: the grouped navigation beside the page.
import type { Me } from "@/lib/gen/api.gen";
import { serverGet } from "@/lib/server-api";
import { SettingsNav } from "./SettingsNav";

export async function SettingsFrame({ children }: { children: React.ReactNode }) {
  const me = await serverGet<Me>("/v1/me");
  return (
    <div className="ws-settings">
      <SettingsNav capabilities={me?.capabilities ?? []} admin={me?.role === "admin"} />
      <div className="ws-settings-page">{children}</div>
    </div>
  );
}
