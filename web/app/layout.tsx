import type { Metadata } from "next";
import Script from "next/script";
import { headers } from "next/headers";
import "./globals.css";

// The proxy creates a request-scoped CSP nonce. Dynamic rendering lets Next attach that nonce
// to its client scripts instead of serving prerendered HTML with unusable nonce-less scripts.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "6th agents",
  description: "Account intelligence workspace",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <Script src="/theme-init.js" strategy="beforeInteractive" nonce={nonce} />
      </head>
      <body>{children}</body>
    </html>
  );
}
