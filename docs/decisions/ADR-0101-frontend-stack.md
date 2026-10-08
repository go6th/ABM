# ADR-0101: Frontend stack (D-91)

Status: proposal

Use Next.js App Router with strict TypeScript. Keep API contracts generated from the OpenAPI snapshot and validate responses at runtime. Own the accessible components in this repository. Use CSS tokens for the design system, TanStack Table for data grids, and Playwright plus axe for browser checks. Motion and React Three Fiber remain optional presentation tools.

Current implementation is partial: Next.js, TypeScript, local fonts, CSS tokens, Lucide icons and generated Zod operation schemas are present. Tailwind, a complete owned component catalogue, table virtualization, a complete Playwright suite and full F-rule enforcement are not complete.
