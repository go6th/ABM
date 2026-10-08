import Link from "next/link";
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
  const displayName = brand?.display_name ?? "6th agents";

  return (
    <div className="client-frame">
      <a className="skip" href="#main">Skip to content</a>
      <header className="client-header">
        <Link className="wordmark" href="/" aria-label={`${displayName} workspace home`}>
          <span className="mark" aria-hidden="true">6</span>
          <span>{displayName}</span>
          <span className="sub">Workspace</span>
        </Link>
        <nav className="client-nav" aria-label="Workspace navigation">
          <Link href="/">Accounts</Link>
          <Link href="/alerts">Alerts</Link>
          <Link href="/usage">Usage</Link>
          <Link href="/audit">Audit</Link>
          <Link href="/settings">Settings</Link>
        </nav>
      </header>
      <main id="main" className="client-main">{children}</main>
      <footer className="client-footer">Prepared by {brand?.attribution ?? "6th agents"}</footer>
      {accent.ok ? (
        <style nonce={nonce}>{`:root{--brand:${accent.used}}`}</style>
      ) : null}
    </div>
  );
}
