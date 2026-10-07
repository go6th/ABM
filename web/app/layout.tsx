// Root layout: the CSP nonce, the tenant's accent token (validated #rrggbb only), navigation and
// the attribution footer ("6th agents", fixed until D-64; branding cannot remove it).
import type { Metadata } from "next";
import { cookies, headers } from "next/headers";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = { title: "6th agents", description: "Account intelligence" };
export const dynamic = "force-dynamic";

interface Tokens { display_name: string | null; accent: string | null; attribution: string }

async function brandingTokens(): Promise<Tokens | null> {
  const origin = process.env.API_ORIGIN ?? "http://127.0.0.1:8080";
  const jar = await cookies();
  const cookie = jar.toString();
  if (!cookie) return null;
  try {
    const reply = await fetch(`${origin}/v1/branding`, { headers: { cookie }, cache: "no-store" });
    if (!reply.ok) return null;
    return (await reply.json()) as Tokens;
  } catch {
    return null;
  }
}

const HEX = /^#[0-9a-f]{6}$/;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  const tokens = await brandingTokens();
  const accent = tokens?.accent && HEX.test(tokens.accent) ? tokens.accent : null;
  return (
    <html lang="en">
      <head>{accent ? <style nonce={nonce}>{`:root{--accent:${accent}}`}</style> : null}</head>
      <body>
        <a className="skip" href="#main">Skip to content</a>
        <header className="top">
          <span className="brand">{tokens?.display_name ?? "6th agents"}</span>
          <nav aria-label="Main">
            <Link href="/">Accounts</Link>
            <Link href="/settings">Settings</Link>
            <Link href="/settings/users">Users</Link>
            <Link href="/settings/sso">Single sign-on</Link>
            <Link href="/settings/connectors">Connectors</Link>
            <Link href="/audit">Audit</Link>
            <Link href="/usage">Usage</Link>
          </nav>
        </header>
        <main id="main">{children}</main>
        <footer className="bottom">Prepared by {tokens?.attribution ?? "6th agents"}</footer>
      </body>
    </html>
  );
}
