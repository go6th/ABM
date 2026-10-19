const d = require('docx');
const { Paragraph, TextRun, Table, TableRow, TableCell, WidthType, ShadingType, HeadingLevel, BorderStyle,
  LevelFormat, AlignmentType, Footer, PageNumber, VerticalAlign, ExternalHyperlink, PageOrientation, Document } = d;
const FONT = "Arial", NAVY = "1F3A5F";
const border = { style: BorderStyle.SINGLE, size: 4, color: "BFC5CC" };
const borders = { top: border, bottom: border, left: border, right: border };
const run = (t, o = {}) => new TextRun({ text: t, font: FONT, size: o.size || 20, bold: o.bold, italics: o.italics, color: o.color });
const p = (t, o = {}) => new Paragraph({ spacing: { after: o.after ?? 120, line: 280 }, children: [run(t, o)] });
const title = (t, sub) => [
  new Paragraph({ spacing: { after: 80 }, children: [new TextRun({ text: t, font: FONT, size: 40, bold: true, color: NAVY })] }),
  new Paragraph({ spacing: { after: 240 }, border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: "E07B39", space: 6 } }, children: [run(sub, { size: 22, color: "555555" })] })];
const h1 = (t, pb) => new Paragraph({ pageBreakBefore: !!pb, heading: HeadingLevel.HEADING_1, spacing: { before: 320, after: 140 }, keepNext: true, children: [new TextRun({ text: t, font: FONT, size: 28, bold: true, color: NAVY })] });
const h2 = t => new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 220, after: 100 }, keepNext: true, children: [new TextRun({ text: t, font: FONT, size: 23, bold: true, color: NAVY })] });
const bullet = t => new Paragraph({ numbering: { reference: "b", level: 0 }, spacing: { after: 70, line: 276 }, children: [run(t)] });
const num = t => new Paragraph({ numbering: { reference: "n", level: 0 }, spacing: { after: 70, line: 276 }, children: [run(t)] });
const labeled = (label, text) => new Paragraph({ spacing: { after: 90, line: 276 }, children: [run(label + " ", { bold: true }), run(text)] });
const link = (text, url) => new Paragraph({ spacing: { after: 40, line: 252 }, children: [new ExternalHyperlink({ link: url, children: [new TextRun({ text, font: FONT, size: 17, color: "1155CC", underline: {} })] })] });
const GRADE = { Confirmed: "DDEFE0", Probable: "FFF1CC", Hypothesis: "FBDAD4", Lead: "E6E6E6", Done: "DDEFE0", Partial: "FFF1CC", "Not done": "FBDAD4", Blocked: "E6E6E6", None: "E6E6E6" };
function cell(content, width, o = {}) {
  const items = Array.isArray(content) ? content : [content];
  const kids = items.map(t => (typeof t === "string")
    ? new Paragraph({ spacing: { after: 40, line: 252 }, children: [run(t, { size: 17, bold: o.bold, color: o.color })] }) : t);
  let fill = o.fill; if (!fill && typeof content === "string" && GRADE[content]) fill = GRADE[content];
  return new TableCell({ width: { size: width, type: WidthType.DXA }, borders, verticalAlign: VerticalAlign.TOP,
    shading: fill ? { type: ShadingType.CLEAR, fill, color: "auto" } : undefined,
    margins: { top: 70, bottom: 70, left: 100, right: 100 }, children: kids });
}
function table(widths, header, rows) {
  const W = widths.reduce((a, b) => a + b, 0);
  return new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: widths, rows: [
    new TableRow({ tableHeader: true, cantSplit: true, children: header.map((h, i) => cell(h, widths[i], { bold: true, fill: NAVY, color: "FFFFFF" })) }),
    ...rows.map(r => new TableRow({ cantSplit: true, children: r.map((c, i) => cell(c, widths[i], { bold: i === 0 && typeof c === "string" && !GRADE[c] })) })) ] });
}
function makeDoc(children, landscape) {
  return new Document({
    styles: { default: { document: { run: { font: FONT, size: 20 } } } },
    numbering: { config: [
      { reference: "b", levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] },
      { reference: "n", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 360 } } } }] } ] },
    sections: [{ properties: { page: { size: landscape ? { width: 12240, height: 15840, orientation: PageOrientation.LANDSCAPE } : { width: 12240, height: 15840 }, margin: { top: 1300, bottom: 1300, left: 1440, right: 1440 } } },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [new TextRun({ children: ["Page ", PageNumber.CURRENT], font: FONT, size: 16, color: "777777" })] })] }) },
      children }] });
}
module.exports = { d, run, p, title, h1, h2, bullet, num, labeled, link, cell, table, makeDoc };
