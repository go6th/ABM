// ESLint for the client app: Next.js core web vitals and TypeScript flat configs, plus a ban on
// raw HTML injection (VW4: untrusted text is only ever rendered as React text nodes).
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  { ignores: [".next/**", ".next-parity/**", "node_modules/**", "playwright-report/**", "test-results/**", "next-env.d.ts", "components/ProviderKey.tsx"] },
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      "react/no-danger": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
    },
  },
];

export default config;
