// Settings: the workspace's own model key. Admins only; the API refuses anyone else.
import type { Me } from "@/lib/gen/api.gen";
import { serverGet } from "@/lib/server-api";
import { ProviderKeyManager } from "@/components/ProviderKey";

export const dynamic = "force-dynamic";

export default async function ModelKeySettings() {
  const me = await serverGet<Me>("/v1/me", "/settings/model-key");
  return (
    <div className="ws-column">
      <h1 className="ws-display">Provider key</h1>
      <p className="ws-muted ws-key-sub">Use your own model key for conversations in this workspace.</p>
      {me?.role === "admin" ? <ProviderKeyManager /> : <p>Only admins manage the provider key.</p>}
    </div>
  );
}
