// node dist/cli.js <bundle.json> <out_dir> [--internal]
// Validates the bundle and writes the Account Summary, the Sources and Evidence file, the Lane
// Highlights when the bundle has them and, only with --internal, the internal-only file.
// Exit codes: 0 written, 1 usage or render error, 2 invalid bundle.
import { readFileSync } from "node:fs";
import { parseBundle } from "./bundle.js";
import { renderBundle } from "./render.js";

function usage(): never {
  console.error("usage: node dist/cli.js <bundle.json> <out_dir> [--internal]");
  process.exit(1);
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const internal = args.includes("--internal");
  const rest = args.filter((a) => a !== "--internal");
  if (rest.length !== 2 || rest.some((a) => a.startsWith("--"))) usage();
  const [bundlePath, outDir] = rest as [string, string];
  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(bundlePath, "utf8"));
  } catch (e) {
    console.error(`cannot read ${bundlePath}: ${(e as Error).message}`);
    process.exit(2);
  }
  const parsed = parseBundle(raw);
  if (!parsed.ok) {
    console.error(`invalid bundle (${parsed.issues.length} problems):`);
    for (const i of parsed.issues.slice(0, 40)) console.error(`  ${i.path}: ${i.message}`);
    process.exit(2);
  }
  const written = await renderBundle(parsed.bundle, outDir, { internal });
  for (const n of written) console.log(n);
}

main().catch((e: unknown) => {
  console.error(`render failed: ${(e as Error).message}`);
  process.exit(1);
});
