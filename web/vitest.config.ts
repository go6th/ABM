// Unit tests for the client's pure modules (query builder, safe rendering).
import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  // Forked Vitest workers race while loading Next.js server modules from Vite's temporary SSR
  // directory in this repo. Threads keep per-file isolation and avoid that filesystem race.
  test: { include: ["tests/unit/**/*.test.ts", "tests/unit/**/*.test.tsx"], environment: "node", pool: "threads",
    exclude: ["**/node_modules/**", "tests/unit/components.test.tsx", "tests/unit/research-files.test.tsx", "tests/unit/run-stop.test.tsx", "tests/unit/ws-components.test.tsx", "tests/unit/ui-tokens.test.ts", "tests/unit/ws-copy.test.ts", "tests/unit/documents.test.tsx"] },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, ".") } },
});
