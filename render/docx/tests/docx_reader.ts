// A small reader for the tests: opens a .docx with jszip, parses document.xml in order and
// returns the body as blocks (paragraphs with style, text, list and first run; tables with their
// shape, cell texts and fills), the page setup and the footer XML. The docx package writes files
// and does not read them, so the tests look at the XML directly.
import JSZip from "jszip";
import { XMLParser } from "fast-xml-parser";

type Node = Record<string, unknown>;

const parser = new XMLParser({ preserveOrder: true, ignoreAttributes: false, attributeNamePrefix: "", trimValues: false, parseTagValue: false });

const tagOf = (n: Node): string => Object.keys(n).find((k) => k !== ":@") ?? "";
const kids = (n: Node): Node[] => (n[tagOf(n)] as Node[] | undefined) ?? [];
const attrs = (n: Node): Record<string, string> => (n[":@"] as Record<string, string> | undefined) ?? {};

function find(n: Node, tag: string): Node | undefined {
  for (const k of kids(n)) if (tagOf(k) === tag) return k;
  return undefined;
}

function findDeep(n: Node, tag: string): Node | undefined {
  for (const k of kids(n)) {
    if (tagOf(k) === tag) return k;
    const d = findDeep(k, tag);
    if (d) return d;
  }
  return undefined;
}

function textOf(n: Node): string {
  if (tagOf(n) === "w:t") return kids(n).map((k) => String((k as Node)["#text"] ?? "")).join("");
  return kids(n).map(textOf).join("");
}

export interface ParaBlock {
  kind: "p";
  style: string | null;
  text: string;
  list: boolean;
  pageBreakBefore: boolean;
  size: number | null;
  bold: boolean;
  color: string | null;
  hyperlinkIds: string[];
}

export interface TableBlock {
  kind: "tbl";
  rows: number;
  cols: number;
  cells: string[][];
  fills: (string | null)[][];
  widths: number[];
}

export type Block = ParaBlock | TableBlock;

function para(n: Node): ParaBlock {
  const pPr = find(n, "w:pPr");
  const style = pPr && find(pPr, "w:pStyle") ? attrs(find(pPr, "w:pStyle") as Node)["w:val"] ?? null : null;
  const firstRun = find(n, "w:r") ?? findDeep(n, "w:r");
  const rPr = firstRun ? find(firstRun, "w:rPr") : undefined;
  const sz = rPr && find(rPr, "w:sz") ? Number(attrs(find(rPr, "w:sz") as Node)["w:val"]) / 2 : null;
  const color = rPr && find(rPr, "w:color") ? attrs(find(rPr, "w:color") as Node)["w:val"] ?? null : null;
  const links = kids(n).filter((k) => tagOf(k) === "w:hyperlink").map((k) => attrs(k)["r:id"] ?? "");
  return {
    kind: "p", style, text: textOf(n), list: !!(pPr && find(pPr, "w:numPr")), pageBreakBefore: !!(pPr && find(pPr, "w:pageBreakBefore")),
    size: sz, bold: !!(rPr && find(rPr, "w:b")), color, hyperlinkIds: links,
  };
}

function tbl(n: Node): TableBlock {
  const rows = kids(n).filter((k) => tagOf(k) === "w:tr");
  const grid = find(n, "w:tblGrid");
  const widths = grid ? kids(grid).map((g) => Number(attrs(g)["w:w"])) : [];
  const cells = rows.map((r) => kids(r).filter((c) => tagOf(c) === "w:tc").map((c) => kids(c).filter((k) => tagOf(k) === "w:p").map(textOf).join("\n")));
  const fills = rows.map((r) => kids(r).filter((c) => tagOf(c) === "w:tc").map((c) => {
    const pr = find(c, "w:tcPr");
    const shd = pr ? find(pr, "w:shd") : undefined;
    return shd ? attrs(shd)["w:fill"] ?? null : null;
  }));
  return { kind: "tbl", rows: rows.length, cols: widths.length, cells, fills, widths };
}

export interface DocxRead {
  blocks: Block[];
  headings: string[];
  tables: TableBlock[];
  page: { width: number; height: number; orient: string | null; margins: Record<string, number> };
  footerXml: string;
  rels: Record<string, string>;
  coreXml: string;
  names: string[];
}

export async function readDocx(bytes: Buffer): Promise<DocxRead> {
  const zip = await JSZip.loadAsync(bytes);
  const xml = await zip.file("word/document.xml")!.async("string");
  const root = parser.parse(xml) as Node[];
  const doc = root.find((n) => tagOf(n) === "w:document") as Node;
  const body = find(doc, "w:body") as Node;
  const blocks: Block[] = [];
  let page: DocxRead["page"] = { width: 0, height: 0, orient: null, margins: {} };
  for (const k of kids(body)) {
    const t = tagOf(k);
    if (t === "w:p") blocks.push(para(k));
    else if (t === "w:tbl") blocks.push(tbl(k));
    else if (t === "w:sectPr") {
      const sz = find(k, "w:pgSz");
      const mar = find(k, "w:pgMar");
      const a = sz ? attrs(sz) : {};
      const m = mar ? attrs(mar) : {};
      page = {
        width: Number(a["w:w"]), height: Number(a["w:h"]), orient: a["w:orient"] ?? null,
        margins: { top: Number(m["w:top"]), bottom: Number(m["w:bottom"]), left: Number(m["w:left"]), right: Number(m["w:right"]) },
      };
    }
  }
  const footerNames = Object.keys(zip.files).filter((n) => /^word\/footer\d*\.xml$/.test(n));
  const footerXml = (await Promise.all(footerNames.map((n) => zip.file(n)!.async("string")))).join("");
  const relsXml = await zip.file("word/_rels/document.xml.rels")!.async("string");
  const rels: Record<string, string> = {};
  for (const m of relsXml.matchAll(/<Relationship\b[^>]*\bId="([^"]+)"[^>]*\bTarget="([^"]+)"/g)) rels[m[1]!] = m[2]!.replace(/&amp;/g, "&");
  for (const m of relsXml.matchAll(/<Relationship\b[^>]*\bTarget="([^"]+)"[^>]*\bId="([^"]+)"/g)) rels[m[2]!] = m[1]!.replace(/&amp;/g, "&");
  const coreXml = await zip.file("docProps/core.xml")!.async("string");
  const headings = blocks.filter((b): b is ParaBlock => b.kind === "p" && !!b.style && b.style.startsWith("Heading")).map((b) => b.text);
  return { blocks, headings, tables: blocks.filter((b): b is TableBlock => b.kind === "tbl"), page, footerXml, rels, coreXml, names: Object.keys(zip.files) };
}
