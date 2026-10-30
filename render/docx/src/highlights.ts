// The Lane Highlights file: a portrait page with the title, the subtitle the bundle gives, and
// one heading with its bullets per section. No tables and no source IDs; it reads as a note.
import type { Document } from "docx";
import { meta, type AccountBundle } from "./bundle.js";
import { bullet, h1, makeDoc, title, type Block } from "./lib.js";
import { cl } from "./text.js";

export function buildHighlights(b: AccountBundle): Document | null {
  const H = b.prose.lane_highlights;
  if (!H) return null;
  const m = meta(b);
  const ch: Block[] = [];
  ch.push(...title(`${m.prefix}: leading opportunities and signals by lane`, cl(H.subtitle)));
  for (const s of H.sections) {
    ch.push(h1(cl(s.heading)));
    for (const t of s.bullets) ch.push(bullet(cl(t)));
  }
  return makeDoc(ch, false);
}
