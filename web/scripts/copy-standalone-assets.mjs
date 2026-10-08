import { cp, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.join(root, ".next", "standalone");
const staticAssets = path.join(root, ".next", "static");
await cp(staticAssets, path.join(output, ".next", "static"), { recursive: true, force: true });

try {
  await access(path.join(root, "public"));
  await cp(path.join(root, "public"), path.join(output, "public"), { recursive: true, force: true });
} catch {
  // This app has no public directory; all static assets are emitted by Next under .next/static.
}
