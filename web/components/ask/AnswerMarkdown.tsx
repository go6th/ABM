"use client";
// The text of an answer, rendered from Markdown. The API sends each paragraph as parts: plain
// text (which may hold Markdown the model wrote: a table, a list, a code block, emphasis), bold
// runs and numbered citations. The parts become one Markdown document per paragraph; a citation
// travels through the parser as a link to #cite-<n> and comes back out as the caller's chip.
// Model output is untrusted (VW4): raw HTML is dropped by rehype-sanitize, images and form
// controls are never rendered, and a link survives only as an absolute http(s) address
// (safeLink) that opens in a new tab without a referrer. No inline styles: the CSP forbids them.
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { LINK_REL, safeLink } from "@/lib/render";
import type { StreamPart } from "@/lib/sse";

const CITE_HREF = /^#cite-(\d{1,3})$/;
const NEVER = new Set(["img", "input", "picture", "source", "video", "audio", "iframe"]);

// GitHub's sanitising schema without media or form controls; links are http(s) only (the cite
// placeholder is a bare fragment, which carries no protocol).
export const SCHEMA = {
  ...defaultSchema,
  tagNames: (defaultSchema.tagNames ?? []).filter((t) => !NEVER.has(t)),
  protocols: { ...defaultSchema.protocols, href: ["http", "https"] },
};

// One paragraph's parts as Markdown: bold parts back in their stars, citations as links to
// #cite-<n> (the only link form that comes back as a chip), text as the model wrote it.
export function toMarkdown(parts: StreamPart[]): string {
  return parts
    .map((p) => {
      if (p.t === "cite") return ` [${p.v}](#cite-${p.v}) `;
      if (p.t === "bold") return `**${p.v}**`;
      return p.v;
    })
    .join("")
    .replace(/\s+/g, " ")
    .trim();
}

// Some models return a complete table as tab-separated text instead of Markdown. Normalize
// consecutive, consistently shaped tab rows before ReactMarkdown parses them so every row stays
// visible as a table in the answer. Fenced code remains untouched.
export function renderTabularText(source: string): string {
  const lines = source.split(/\r?\n/);
  const out: string[] = [];
  let fenced: "```" | "~~~" | null = null;
  for (let i = 0; i < lines.length; ) {
    const fence = lines[i]!.trimStart().startsWith("```")
      ? "```"
      : lines[i]!.trimStart().startsWith("~~~")
        ? "~~~"
        : null;
    if (fence) {
      fenced = fenced === fence ? null : fenced ?? fence;
      out.push(lines[i]!);
      i += 1;
      continue;
    }
    if (fenced || !lines[i]!.includes("\t")) {
      out.push(lines[i]!);
      i += 1;
      continue;
    }

    const start = i;
    const rows: string[][] = [];
    while (i < lines.length && lines[i]!.includes("\t")) {
      rows.push(lines[i]!.split("\t").map((cell) => cell.trim()));
      i += 1;
    }
    const width = rows[0]!.length;
    if (rows.length < 2 || width < 2 || rows.some((row) => row.length !== width)) {
      out.push(...lines.slice(start, i));
      continue;
    }
    const cell = (value: string) => value.replace(/\\/g, "\\\\").replace(/\|/g, "\\|");
    const markdown = (row: string[]) => `| ${row.map(cell).join(" | ")} |`;
    out.push(
      "",
      markdown(rows[0]!),
      markdown(rows[0]!.map(() => "---")),
      ...rows.slice(1).map(markdown),
      "",
    );
  }
  return out.join("\n");
}

// A single line that must stay a line (an item in the items card): a leading list marker or
// heading mark is escaped so the Markdown parser leaves the line as it is, bold and all.
export function inlineMarkdown(text: string): string {
  return text
    .replace(/^(\s*)(\d{1,3})\.(\s)/, "$1$2\\.$3")
    .replace(/^(\s*)([-*+>])(\s)/, "$1\\$2$3")
    .replace(/^(\s*)(#{1,6}\s)/, "$1\\$2");
}

type AnchorProps = ComponentPropsWithoutRef<"a"> & { node?: unknown };
type PlainProps = { children?: ReactNode; node?: unknown };
type CellProps = ComponentPropsWithoutRef<"td"> & { node?: unknown };

// A table column's alignment reaches the cell as a style; the CSP forbids inline styles, so it
// becomes a class instead (and the style never reaches the page).
function cellClass(style: CellProps["style"], align: CellProps["align"]): string | undefined {
  const a = style?.textAlign ?? align;
  return a === "center" ? "md-c" : a === "right" ? "md-r" : undefined;
}
const Td = ({ children, style, align }: CellProps) => <td className={cellClass(style, align)}>{children}</td>;
const Th = ({ children, style, align }: CellProps) => <th className={cellClass(style, align)}>{children}</th>;
const Table = ({ children }: PlainProps) => <div className="md-table"><table>{children}</table></div>;

export function AnswerMarkdown({
  parts, cite, inline = false,
}: {
  parts: StreamPart[];
  // the chip for citation n (clickable in a finished answer, static while it streams)
  cite: (n: string) => ReactNode;
  // one line, no block structure: the paragraph wrapper is dropped
  inline?: boolean;
}) {
  const components: Components = {
    a: ({ href, children }: AnchorProps) => {
      const m = CITE_HREF.exec(href ?? "");
      if (m) return cite(m[1]!);
      const safe = safeLink(href);
      if (!safe) return <span>{children}</span>;
      return <a className="md-link" href={safe} target="_blank" rel={LINK_REL}>{children}</a>;
    },
    table: Table,
    td: Td,
    th: Th,
    ...(inline ? { p: ({ children }: PlainProps) => <>{children}</> } : {}),
  };
  const markdown = renderTabularText(toMarkdown(parts));
  const source = inline ? inlineMarkdown(markdown) : markdown;
  return (
    <div className={inline ? "md inline" : "md"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeSanitize, SCHEMA]]} components={components}>
        {source}
      </ReactMarkdown>
    </div>
  );
}
