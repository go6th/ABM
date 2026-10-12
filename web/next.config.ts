// Next.js configuration for the 6th agents client app (Phase 3 WS5).
// The browser talks only to this origin: /v1 is rewritten to the API, so the session cookie is
// first-party and the CSRF token never crosses origins. Security headers on every response;
// the Content-Security-Policy with a per-request nonce is set in proxy.ts.
import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  // A second dev server (the parity suite) builds into its own directory so it does not take
  // the lock of a developer's running `next dev`.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  // *.dev.tsx files are pages only in development (the /__design gallery); a production build
  // has no such routes and none of their sample content.
  pageExtensions: process.env.NODE_ENV === "production" ? ["tsx", "ts"] : ["dev.tsx", "tsx", "ts"],
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default config;
