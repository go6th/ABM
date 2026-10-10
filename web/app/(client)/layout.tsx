import { cookies, headers } from "next/headers";
import { tenantAccent } from "@/design/contrast";
import { redirect } from "next/navigation";
import { Me } from "@/lib/gen/api.gen";
import { ClientShell } from "@/components/ClientShell";

export const dynamic = "force-dynamic";

interface BrandReply {
  display_name: string | null;
  accent: string | null;
  attribution: string;
}

async function getBranding(): Promise<BrandReply | null> {
  const origin = process.env.API_ORIGIN ?? "http://127.0.0.1:8080";
  const cookie = (await cookies()).toString();
  if (!cookie) return null;
  try {
    const response = await fetch(`${origin}/v1/branding`, {
      headers: { cookie },
      cache: "no-store",
    });
    if (!response.ok) return null;
    const value: unknown = await response.json();
    if (typeof value !== "object" || value === null) return null;
    const record = value as Record<string, unknown>;
    if (typeof record.attribution !== "string") return null;
    return {
      display_name: typeof record.display_name === "string" ? record.display_name : null,
      accent: typeof record.accent === "string" ? record.accent : null,
      attribution: record.attribution,
    };
  } catch {
    return null;
  }
}

async function getMe(): Promise<Me | null> {
  const origin = process.env.API_ORIGIN ?? "http://127.0.0.1:8080";
  const cookie = (await cookies()).toString();
  if (!cookie) return null;
  try {
    const response = await fetch(`${origin}/v1/me`, {
      headers: { cookie, accept: "application/json" },
      cache: "no-store",
      redirect: "error",
    });
    if (response.status === 401) return null;
    if (!response.ok) throw new Error(`Current-user request failed (${response.status}).`);
    return Me.parse(await response.json());
  } catch {
    throw new Error("Workspace access could not be verified. Reload to try again.");
  }
}

export default async function ClientLayout({ children }: { children: React.ReactNode }) {
  const [brand, me, requestHeaders] = await Promise.all([getBranding(), getMe(), headers()]);
  if (!me) redirect("/login");
  const accent = tenantAccent(brand?.accent);
  const nonce = requestHeaders.get("x-nonce") ?? undefined;
  const displayName = brand?.display_name ?? "6th agents";
  const attribution = brand?.attribution ?? "6th agents";

  return (
    <>
      <ClientShell me={me} displayName={displayName} attribution={attribution}>
        {children}
      </ClientShell>
      {accent.ok ? <style nonce={nonce}>{`:root{--brand:${accent.used}}`}</style> : null}
    </>
  );
}
