import { cookies, headers } from "next/headers";
import { tenantAccent } from "@/design/contrast";

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

export default async function ClientLayout({ children }: { children: React.ReactNode }) {
  const [brand, requestHeaders] = await Promise.all([getBranding(), headers()]);
  const accent = tenantAccent(brand?.accent);
  const nonce = requestHeaders.get("x-nonce") ?? undefined;

  return (
    <>
      {children}
      {accent.ok ? (
        <style nonce={nonce}>{`:root{--brand:${accent.used}}`}</style>
      ) : null}
    </>
  );
}
