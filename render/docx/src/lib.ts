// A port of the reference workspace's docx_lib.js: the fonts, sizes, colours, spacing, the two
// numbering references (b for bullets, n for numbered lists), table cells with their margins,
// borders and grade fills, the landscape or portrait page with its margins, and the footer with a
// live page number field. Everything a Word file of the parity system is built from.
import {
  AlignmentType, BorderStyle, Document, ExternalHyperlink, Footer, HeadingLevel, LevelFormat,
  PageNumber, PageOrientation, Paragraph, ShadingType, Table, TableCell, TableRow, TextRun,
  VerticalAlign, WidthType, type IBorderOptions,
} from "docx";

export const FONT = "Arial";
export const NAVY = "1F3A5F";
const LINK_BLUE = "1155CC";
const border: IBorderOptions = { style: BorderStyle.SINGLE, size: 4, color: "BFC5CC" };
const borders = { top: border, bottom: border, left: border, right: border };

export type Block = Paragraph | Table;

export interface RunOptions {
  size?: number;
  bold?: boolean;
  italics?: boolean;
  color?: string;
}

export interface ParagraphOptions extends RunOptions {
  after?: number;
}

export const run = (t: string, o: RunOptions = {}): TextRun =>
  new TextRun({ text: t, font: FONT, size: o.size ?? 20, bold: o.bold, italics: o.italics, color: o.color });

export const p = (t: string, o: ParagraphOptions = {}): Paragraph =>
  new Paragraph({ spacing: { after: o.after ?? 120, line: 280 }, children: [run(t, o)] });

// The file title in navy over a subtitle in grey with an orange rule under it.
export const title = (t: string, sub: string): Paragraph[] => [
  new Paragraph({ spacing: { after: 80 }, children: [new TextRun({ text: t, font: FONT, size: 40, bold: true, color: NAVY })] }),
  new Paragraph({
    spacing: { after: 240 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: "E07B39", space: 6 } },
    children: [run(sub, { size: 22, color: "555555" })],
  }),
];

export const h1 = (t: string, pageBreak = false): Paragraph =>
  new Paragraph({
    pageBreakBefore: pageBreak, heading: HeadingLevel.HEADING_1, spacing: { before: 320, after: 140 }, keepNext: true,
    children: [new TextRun({ text: t, font: FONT, size: 28, bold: true, color: NAVY })],
  });

export const h2 = (t: string): Paragraph =>
  new Paragraph({
    heading: HeadingLevel.HEADING_2, spacing: { before: 220, after: 100 }, keepNext: true,
    children: [new TextRun({ text: t, font: FONT, size: 23, bold: true, color: NAVY })],
  });

export const bullet = (t: string): Paragraph =>
  new Paragraph({ numbering: { reference: "b", level: 0 }, spacing: { after: 70, line: 276 }, children: [run(t)] });

export const num = (t: string): Paragraph =>
  new Paragraph({ numbering: { reference: "n", level: 0 }, spacing: { after: 70, line: 276 }, children: [run(t)] });

export const labeled = (label: string, text: string): Paragraph =>
  new Paragraph({ spacing: { after: 90, line: 276 }, children: [run(`${label} `, { bold: true }), run(text)] });

// A link paragraph, used on its own and inside table cells.
export const link = (text: string, url: string): Paragraph =>
  new Paragraph({
    spacing: { after: 40, line: 252 },
    children: [new ExternalHyperlink({ link: url, children: [new TextRun({ text, font: FONT, size: 17, color: LINK_BLUE, underline: {} })] })],
  });

// Cell fills for a grade or a coverage status.
export const GRADE: Readonly<Record<string, string>> = {
  Confirmed: "DDEFE0", Probable: "FFF1CC", Hypothesis: "FBDAD4", Lead: "E6E6E6",
  Done: "DDEFE0", Partial: "FFF1CC", "Not done": "FBDAD4", Blocked: "E6E6E6", None: "E6E6E6",
};
const gradeFill = (s: string): string | undefined => (Object.hasOwn(GRADE, s) ? GRADE[s] : undefined);

export type CellContent = string | Paragraph | readonly (string | Paragraph)[];

export interface CellOptions {
  bold?: boolean;
  color?: string;
  fill?: string;
}

export function cell(content: CellContent, width: number, o: CellOptions = {}): TableCell {
  const items = Array.isArray(content) ? (content as readonly (string | Paragraph)[]) : [content as string | Paragraph];
  const kids = items.map((t) =>
    typeof t === "string"
      ? new Paragraph({ spacing: { after: 40, line: 252 }, children: [run(t, { size: 17, bold: o.bold, color: o.color })] })
      : t);
  let fill = o.fill;
  if (!fill && typeof content === "string") fill = gradeFill(content);
  return new TableCell({
    width: { size: width, type: WidthType.DXA }, borders, verticalAlign: VerticalAlign.TOP,
    shading: fill ? { type: ShadingType.CLEAR, fill, color: "auto" } : undefined,
    margins: { top: 70, bottom: 70, left: 100, right: 100 }, children: kids,
  });
}

// A table with a navy header row that repeats on every page; the first column of a body row is
// bold unless it holds a grade.
export function table(widths: readonly number[], header: readonly string[], rows: readonly (readonly CellContent[])[]): Table {
  const total = widths.reduce((a, b) => a + b, 0);
  const w = (i: number): number => widths[i] ?? 0;
  return new Table({
    width: { size: total, type: WidthType.DXA }, columnWidths: [...widths],
    rows: [
      new TableRow({ tableHeader: true, cantSplit: true, children: header.map((h, i) => cell(h, w(i), { bold: true, fill: NAVY, color: "FFFFFF" })) }),
      ...rows.map((r) => new TableRow({
        cantSplit: true,
        children: r.map((c, i) => cell(c, w(i), { bold: i === 0 && typeof c === "string" && gradeFill(c) === undefined })),
      })),
    ],
  });
}

export interface DocOptions {
  creator?: string;
  description?: string;
}

// The document: Arial 10 by default, the two lists, one section on US Letter (landscape when
// asked) with the reference's margins, and a right-aligned "Page N" footer whose number is a
// live field.
export function makeDoc(children: readonly Block[], landscape: boolean, o: DocOptions = {}): Document {
  return new Document({
    creator: o.creator ?? "Account research",
    lastModifiedBy: o.creator ?? "Account research",
    description: o.description ?? "Rendered from the account bundle",
    styles: { default: { document: { run: { font: FONT, size: 20 } } } },
    numbering: {
      config: [
        { reference: "b", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] },
        { reference: "n", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 360 } } } }] },
      ],
    },
    sections: [{
      properties: {
        page: {
          size: landscape ? { width: 12240, height: 15840, orientation: PageOrientation.LANDSCAPE } : { width: 12240, height: 15840 },
          margin: { top: 1300, bottom: 1300, left: 1440, right: 1440 },
        },
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.RIGHT,
            children: [new TextRun({ children: ["Page ", PageNumber.CURRENT], font: FONT, size: 16, color: "777777" })],
          })],
        }),
      },
      children: [...children],
    }],
  });
}
