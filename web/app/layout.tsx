import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import "@/ui/tokens.css";
import "@/ui/base.css";
import "@/ui/components.css";
import "@/ui/shell.css";
import "@/ui/auth.css";
import { THEME_COOKIE, parseTheme, themeAttribute } from "@/ui/theme";

// The proxy creates a request-scoped CSP nonce. Dynamic rendering lets Next attach that nonce
// to its client scripts instead of serving prerendered HTML with unusable nonce-less scripts.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Account Intelligence by 6th",
  description: "Account intelligence workspace",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#FFFFFF" },
    { media: "(prefers-color-scheme: dark)", color: "#0B0B0F" },
  ],
};

// The theme comes from the ai_theme cookie on the server, so no script runs before paint.
export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const theme = themeAttribute(parseTheme((await cookies()).get(THEME_COOKIE)?.value));
  return (
    <html lang="en" data-theme={theme}>
      <body>{children}</body>
    </html>
  );
}
