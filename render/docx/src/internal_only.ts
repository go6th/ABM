// The internal-only file, a port of the reference's internal-only script: what the pursuing
// client's own records say about the account. Portrait, never in the zip or a share, and only
// written when the command line asks for it.
import type { Document } from "docx";
import { meta, type AccountBundle } from "./bundle.js";
import { bullet, h1, labeled, makeDoc, p, title, type Block } from "./lib.js";

export function buildInternalOnly(b: AccountBundle): Document | null {
  const I = b.prose.internal_only;
  if (!I) return null;
  const m = meta(b);
  const ch: Block[] = [];
  ch.push(...title(`Internal Only: ${m.prefix} (${m.legal})`,
    `Internal notes from the pursuing client's own records, ${I.dates}. Not for the client share and not in the workspace zip. ${m.byline}`));
  ch.push(h1(`What was found in the ${m.client}'s records`));
  for (const [label, text] of I.items) ch.push(labeled(label ?? "", text ?? ""));
  ch.push(h1("Why it matters"));
  for (const t of I.why) ch.push(bullet(t));
  ch.push(h1("What the client facing files say instead"));
  ch.push(p(I.instead));
  return makeDoc(ch, false);
}
