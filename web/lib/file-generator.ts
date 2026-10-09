// Client-side file generator supporting PDF, CSV, and JSON downloads.
// Pure TypeScript implementation requiring zero external libraries.

export interface PdfSection {
  type: "heading" | "paragraph" | "bullet" | "table";
  text?: string;
  level?: 1 | 2;
  headers?: string[];
  rows?: (string | number)[][];
  colWidths?: number[];
}

export interface PdfReportOptions {
  filename: string;
  title: string;
  subtitle: string;
  sections: PdfSection[];
  confidentialNote?: string;
}

class PdfDocument {
  private pages: string[] = [];
  private currentCommands: string[] = [];
  private readonly pageWidth = 612; // Standard Letter width (points)
  private readonly pageHeight = 792; // Standard Letter height (points)
  private readonly marginLeft = 45;
  private readonly marginRight = 45;
  private readonly contentWidth = 522; // 612 - 45 - 45
  private y = 742;

  constructor(
    private readonly title: string,
    private readonly subtitle: string,
    private readonly confidentialNote = "Confidential · ABM Account Intelligence",
  ) {
    this.newPage();
  }

  private escape(text: string): string {
    return String(text)
      .replace(/\\/g, "\\\\")
      .replace(/\(/g, "\\(")
      .replace(/\)/g, "\\)");
  }

  private newPage() {
    if (this.currentCommands.length > 0) {
      this.pages.push(this.currentCommands.join("\n"));
      this.currentCommands = [];
    }
    this.y = this.pageHeight - 50;
    this.renderHeader();
  }

  private renderHeader() {
    // Divider rule
    this.currentCommands.push(
      "0.85 0.85 0.88 RG",
      "0.5 w",
      `${this.marginLeft} ${this.y - 30} ${this.contentWidth} 0 re`,
      "S",
      // Title
      "BT",
      "/F2 16 Tf",
      "0.12 0.13 0.16 rg",
      `${this.marginLeft} ${this.y} Td`,
      `(${this.escape(this.title)}) Tj`,
      "ET",
      // Subtitle
      "BT",
      "/F1 9 Tf",
      "0.45 0.47 0.52 rg",
      `${this.marginLeft} ${this.y - 16} Td`,
      `(${this.escape(this.subtitle)}) Tj`,
      "ET",
    );
    this.y -= 45;
  }

  public addHeading(text: string, level: 1 | 2 = 1) {
    if (this.y < 85) this.newPage();
    const size = level === 1 ? 13 : 11;
    this.y -= level === 1 ? 16 : 12;
    this.currentCommands.push(
      "BT",
      `/F2 ${size} Tf`,
      "0.12 0.14 0.18 rg",
      `${this.marginLeft} ${this.y} Td`,
      `(${this.escape(text)}) Tj`,
      "ET",
    );
    this.y -= 10;
  }

  public addParagraph(text: string) {
    const words = text.split(/\s+/);
    let line = "";
    const maxChars = 84;
    for (const w of words) {
      if ((line + " " + w).trim().length > maxChars) {
        this.renderTextLine(line);
        line = w;
      } else {
        line = line ? line + " " + w : w;
      }
    }
    if (line) this.renderTextLine(line);
    this.y -= 6;
  }

  public addBullet(text: string) {
    const words = text.split(/\s+/);
    let line = "";
    const maxChars = 78;
    let isFirst = true;
    for (const w of words) {
      if ((line + " " + w).trim().length > maxChars) {
        this.renderBulletLine(line, isFirst);
        isFirst = false;
        line = w;
      } else {
        line = line ? line + " " + w : w;
      }
    }
    if (line) this.renderBulletLine(line, isFirst);
    this.y -= 4;
  }

  private renderBulletLine(line: string, isFirst: boolean) {
    if (this.y < 60) this.newPage();
    const indent = isFirst ? this.marginLeft : this.marginLeft + 14;
    const prefix = isFirst ? "- " : "";
    this.currentCommands.push(
      "BT",
      "/F1 9.5 Tf",
      "0.22 0.24 0.28 rg",
      `${indent} ${this.y} Td`,
      `(${this.escape(prefix + line)}) Tj`,
      "ET",
    );
    this.y -= 13;
  }

  private renderTextLine(line: string) {
    if (this.y < 60) this.newPage();
    this.currentCommands.push(
      "BT",
      "/F1 9.5 Tf",
      "0.22 0.24 0.28 rg",
      `${this.marginLeft} ${this.y} Td`,
      `(${this.escape(line)}) Tj`,
      "ET",
    );
    this.y -= 13;
  }

  public addTable(headers: string[], rows: (string | number)[][], customColWidths?: number[]) {
    const rowHeight = 18;
    if (this.y < 100) this.newPage();

    const colWidths = customColWidths ?? headers.map(() => Math.floor(this.contentWidth / headers.length));

    // Header bar
    this.currentCommands.push(
      "0.18 0.20 0.24 rg",
      `${this.marginLeft} ${this.y - 14} ${this.contentWidth} ${rowHeight} re`,
      "f",
    );

    let x = this.marginLeft + 4;
    for (let i = 0; i < headers.length; i++) {
      this.currentCommands.push(
        "BT",
        "/F2 8.5 Tf",
        "1 1 1 rg",
        `${x} ${this.y - 9} Td`,
        `(${this.escape(headers[i] ?? "")}) Tj`,
        "ET",
      );
      x += colWidths[i] ?? 80;
    }
    this.y -= rowHeight;

    // Rows
    for (let r = 0; r < rows.length; r++) {
      if (this.y < 60) this.newPage();
      if (r % 2 === 1) {
        this.currentCommands.push(
          "0.96 0.96 0.97 rg",
          `${this.marginLeft} ${this.y - 14} ${this.contentWidth} ${rowHeight} re`,
          "f",
        );
      }
      this.currentCommands.push(
        "0.88 0.88 0.90 RG",
        "0.5 w",
        `${this.marginLeft} ${this.y - 14} ${this.contentWidth} 0 re`,
        "S",
      );

      x = this.marginLeft + 4;
      for (let c = 0; c < headers.length; c++) {
        const val = String(rows[r]?.[c] ?? "").slice(0, 34);
        this.currentCommands.push(
          "BT",
          "/F1 8 Tf",
          "0.16 0.17 0.20 rg",
          `${x} ${this.y - 9} Td`,
          `(${this.escape(val)}) Tj`,
          "ET",
        );
        x += colWidths[c] ?? 80;
      }
      this.y -= rowHeight;
    }
    this.y -= 10;
  }

  public toBlob(): Blob {
    if (this.currentCommands.length > 0) {
      this.pages.push(this.currentCommands.join("\n"));
      this.currentCommands = [];
    }

    const totalPages = this.pages.length;
    const finalizedPages = this.pages.map((pageContent, idx) => {
      const footer = [
        "0.85 0.85 0.88 RG",
        "0.5 w",
        `${this.marginLeft} 35 ${this.contentWidth} 0 re`,
        "S",
        "BT",
        "/F1 8 Tf",
        "0.48 0.50 0.54 rg",
        `${this.marginLeft} 24 Td`,
        `(${this.escape(this.confidentialNote)}) Tj`,
        "ET",
        "BT",
        "/F1 8 Tf",
        "0.48 0.50 0.54 rg",
        `${this.pageWidth - this.marginRight - 55} 24 Td`,
        `(${this.escape(`Page ${idx + 1} of ${totalPages}`)}) Tj`,
        "ET",
      ].join("\n");
      return pageContent + "\n" + footer;
    });

    const objects: string[] = [];
    const pageObjStartId = 5;
    const streamObjStartId = pageObjStartId + totalPages;

    const pageRefs: string[] = [];
    for (let i = 0; i < totalPages; i++) {
      pageRefs.push(`${pageObjStartId + i} 0 R`);
    }

    objects.push("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
    objects.push(`2 0 obj\n<< /Type /Pages /Kids [${pageRefs.join(" ")}] /Count ${totalPages} >>\nendobj\n`);
    objects.push("3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n");
    objects.push("4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n");

    for (let i = 0; i < totalPages; i++) {
      const pageObjId = pageObjStartId + i;
      const streamObjId = streamObjStartId + i;
      objects.push(
        `${pageObjId} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${this.pageWidth} ${this.pageHeight}] /Contents ${streamObjId} 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>\nendobj\n`,
      );
    }

    for (let i = 0; i < totalPages; i++) {
      const streamObjId = streamObjStartId + i;
      const data = finalizedPages[i];
      const encoder = new TextEncoder();
      const encoded = encoder.encode(data);
      objects.push(`${streamObjId} 0 obj\n<< /Length ${encoded.length} >>\nstream\n${data}\nendstream\nendobj\n`);
    }

    const header = "%PDF-1.4\n%\xE2\xE3\xCF\xD3\n";
    let body = "";
    const xrefOffsets: number[] = [0];
    const encoder = new TextEncoder();
    let currentOffset = encoder.encode(header).length;

    for (const obj of objects) {
      xrefOffsets.push(currentOffset);
      body += obj;
      currentOffset += encoder.encode(obj).length;
    }

    const xrefStart = currentOffset;
    let xref = `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
    for (let i = 1; i <= objects.length; i++) {
      xref += `${String(xrefOffsets[i]).padStart(10, "0")} 00000 n \n`;
    }

    const trailer = `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefStart}\n%%EOF\n`;
    const fullContent = header + body + xref + trailer;

    return new Blob([fullContent], { type: "application/pdf" });
  }
}

function triggerDownload(blob: Blob, filename: string) {
  if (typeof window === "undefined") return;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function downloadPdfReport(options: PdfReportOptions) {
  const doc = new PdfDocument(options.title, options.subtitle, options.confidentialNote);
  for (const sec of options.sections) {
    if (sec.type === "heading") {
      doc.addHeading(sec.text ?? "", sec.level ?? 1);
    } else if (sec.type === "paragraph") {
      doc.addParagraph(sec.text ?? "");
    } else if (sec.type === "bullet") {
      doc.addBullet(sec.text ?? "");
    } else if (sec.type === "table" && sec.headers && sec.rows) {
      doc.addTable(sec.headers, sec.rows, sec.colWidths);
    }
  }
  const blob = doc.toBlob();
  triggerDownload(blob, options.filename);
}

export function downloadCsv(filename: string, headers: string[], rows: (string | number)[][]) {
  const formatCell = (val: string | number) => {
    const str = String(val);
    if (str.includes(",") || str.includes('"') || str.includes("\n")) {
      return `"${str.replaceAll('"', '""')}"`;
    }
    return str;
  };

  const headerLine = headers.map(formatCell).join(",");
  const rowLines = rows.map((r) => r.map(formatCell).join(","));
  const csvContent = [headerLine, ...rowLines].join("\r\n");

  const blob = new Blob(["\uFEFF" + csvContent], { type: "text/csv;charset=utf-8;" });
  triggerDownload(blob, filename);
}

export function downloadJson(filename: string, data: unknown) {
  const jsonContent = JSON.stringify(data, null, 2);
  const blob = new Blob([jsonContent], { type: "application/json;charset=utf-8;" });
  triggerDownload(blob, filename);
}
