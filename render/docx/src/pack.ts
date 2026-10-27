// Packs a document into bytes that do not change between renders. The docx package stamps the
// core properties and every zip entry with the clock and gives each hyperlink a random
// relationship ID, so the archive is rebuilt with the run date in both places and the hyperlink
// IDs renumbered in the order they appear. Two renders of the same bundle then give the same
// file, byte for byte.
import { Packer, type Document } from "docx";
import JSZip from "jszip";

function stamp(xml: string, tag: string, iso: string): string {
  const re = new RegExp(`(<${tag}[^>]*>)[^<]*(</${tag}>)`);
  if (!re.test(xml)) throw new Error(`core properties have no ${tag}`);
  return xml.replace(re, `$1${iso}$2`);
}

// A relationship ID sits between quotes. The fixed ones are "rId" and a number; the random
// ones mix letters, digits, "_" and "-", and are renumbered as rIdlnk and a number, which can
// never collide with a fixed one.
const QUOTED_ID = /"(rId[A-Za-z0-9_-]+)"/g;
const isRandom = (id: string): boolean => !/^rId\d+$/.test(id);

function renumber(partXml: string, relsXml: string): [string, string] {
  const map = new Map<string, string>();
  for (const m of partXml.matchAll(QUOTED_ID)) {
    const id = m[1] ?? "";
    if (isRandom(id) && !map.has(id)) map.set(id, `rIdlnk${map.size + 1}`);
  }
  const swap = (xml: string): string => xml.replace(QUOTED_ID, (whole, id: string) => (map.has(id) ? `"${map.get(id)}"` : whole));
  return [swap(partXml), swap(relsXml)];
}

export async function packFixed(doc: Document, when: Date): Promise<Buffer> {
  const bytes = await Packer.toBuffer(doc);
  const src = await JSZip.loadAsync(bytes);
  const iso = when.toISOString();
  const texts = new Map<string, string>();
  for (const name of Object.keys(src.files)) {
    const entry = src.files[name];
    if (entry && !entry.dir) texts.set(name, await entry.async("string"));
  }
  for (const [name, xml] of texts) {
    const m = /^word\/([^/]+\.xml)$/.exec(name);
    const relsName = m ? `word/_rels/${m[1]}.rels` : null;
    const rels = relsName ? texts.get(relsName) : undefined;
    if (relsName && rels !== undefined) {
      const [part, newRels] = renumber(xml, rels);
      texts.set(name, part);
      texts.set(relsName, newRels);
    }
  }
  const core = texts.get("docProps/core.xml");
  if (core === undefined) throw new Error("the package has no core properties");
  texts.set("docProps/core.xml", stamp(stamp(core, "dcterms:created", iso), "dcterms:modified", iso));
  const out = new JSZip();
  for (const [name, xml] of texts) out.file(name, Buffer.from(xml, "utf8"), { date: when, compression: "DEFLATE", createFolders: false });
  return out.generateAsync({ type: "nodebuffer", compression: "DEFLATE", platform: "UNIX" });
}
