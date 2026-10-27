// Renders every Word file of a bundle into a folder and reports what was written.
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileNames, meta, type AccountBundle } from "./bundle.js";
import { buildEvidence } from "./evidence.js";
import { buildHighlights } from "./highlights.js";
import { buildInternalOnly } from "./internal_only.js";
import { packFixed } from "./pack.js";
import { buildSummary } from "./summary.js";

export interface RenderOptions {
  internal?: boolean;
}

export async function renderBundle(b: AccountBundle, outDir: string, o: RenderOptions = {}): Promise<string[]> {
  const m = meta(b);
  const names = fileNames(m.prefix);
  mkdirSync(outDir, { recursive: true });
  const written: string[] = [];
  const write = async (name: string, doc: import("docx").Document | null): Promise<void> => {
    if (!doc) return;
    writeFileSync(join(outDir, name), await packFixed(doc, m.stampedAt));
    written.push(name);
  };
  await write(names.summary, buildSummary(b));
  await write(names.evidence, buildEvidence(b));
  await write(names.highlights, buildHighlights(b));
  if (o.internal) await write(names.internal, buildInternalOnly(b));
  return written;
}
