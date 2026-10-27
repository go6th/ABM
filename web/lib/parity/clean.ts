// The one text cleaner, identical to parity.text (Python) and render/docx/src/text.ts: the
// Word files print prose through clean(), the workbooks through cleanCell() (dashes only) and
// the posting cells through cleanPosting() (marks stripped first). Precedence inside clean():
// dashes, then " - ", then the apostrophe, then the arrow, as the reference's chain ran.

const DASH = /\s*[–—]\s*/g;
const MARKS = /\*\*|##|#/g;

/** The workbook cleaner: en and em dashes (with the spaces around them) become ", ". */
export function cleanCell(text: unknown): string {
  return (text == null ? "" : String(text)).replace(DASH, ", ");
}

/** The Word cleaner: cleanCell plus " - " to ", ", a curly apostrophe to a straight one and
 * the arrow to " to ". */
export function clean(text: unknown): string {
  return cleanCell(text).replace(/ - /g, ", ").replace(/’/g, "'").replace(/→/g, " to ");
}

/** Posting marks (**, ##, #) removed, as the Job Postings builder does before the dashes. */
export function stripPostingMarks(text: unknown): string {
  return (text == null ? "" : String(text)).replace(MARKS, "");
}

/** A posting cell: marks stripped, then the workbook cleaner. */
export function cleanPosting(text: unknown): string {
  return cleanCell(stripPostingMarks(text));
}
