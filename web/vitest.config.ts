// Unit tests for the client's pure modules (query builder, safe rendering).
import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  // Forked Vitest workers race while loading Next.js server modules from Vite's temporary SSR
  // directory in this repo. Threads keep per-file isolation and avoid that filesystem race.
  test: { include: ["tests/unit/**/*.test.ts", "tests/unit/**/*.test.tsx"], environment: "node", pool: "threads" },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, ".") } },
});
