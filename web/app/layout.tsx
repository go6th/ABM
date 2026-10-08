import type { Metadata } from "next";
import "./globals.css";

// The proxy creates a request-scoped CSP nonce. Dynamic rendering lets Next attach that nonce
// to its client scripts instead of serving prerendered HTML with unusable nonce-less scripts.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "6th agents",
  description: "Account intelligence workspace",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
