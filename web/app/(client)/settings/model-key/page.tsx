// Settings: the workspace's own model key. Admins only; the API refuses anyone else.
import type { Me } from "@/lib/gen/api.gen";
import { serverGet } from "@/lib/server-api";
import { ProviderKeyManager } from "@/components/ProviderKey";
import { Banner, PageHead } from "@/ui/primitives";

export const dynamic = "force-dynamic";

export default async function ModelKeySettings() {
  const me = await serverGet<Me>("/v1/me", "/settings/model-key");
  return (
    <div className="reading">
      <PageHead title="Provider key" sub="Use your own model key for conversations in this workspace." />
      {me?.role === "admin" ? <ProviderKeyManager /> : <Banner kind="neutral">Only admins manage the provider key.</Banner>}
    </div>
  );
}
