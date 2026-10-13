import type { Metadata, Viewport } from "next";
import "@/ui/tokens.css";
import "@/ui/base.css";
import "@/ui/components.css";
import "@/ui/shell.css";
import "@/ui/auth.css";

// The proxy creates a request-scoped CSP nonce. Dynamic rendering lets Next attach that nonce
// to its client scripts instead of serving prerendered HTML with unusable nonce-less scripts.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Account Intelligence by 6th",
  description: "Account intelligence workspace",
};

export const viewport: Viewport = { themeColor: "#FFFFFF" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
