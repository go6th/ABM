"use client";
// DataGrid: a plain HTML table. No grid library: sort and filter run on the server through the
// tables API, so the client needs only a column model, row numbers, a sticky header (CSS), copy
// and the download link. Every cell is a React text node; links survive only as checked http(s)
// URLs.
import { useState } from "react";
import { cellText, LINK_REL, safeLink } from "@/lib/render";
import { m } from "@/lib/messages";
import type { Column, QuerySpec } from "@/lib/query";
import { GradeMark, toGrade } from "./GradeMark";

export interface GridRow { position: number; ref: string | null; row_id: string | null; cells: Record<string, unknown>; source: { code: string | null; url: string | null; state: string | null } }

function Cell({ column, value }: { column: Column; value: unknown }) {
  if (column.key === "grade" || column.kind === "grade") {
    const grade = toGrade(value);
    return grade ? <GradeMark grade={grade} /> : <>{cellText(value)}</>;
  }
  const link = column.kind === "url" ? safeLink(value) : null;
  if (link) return <a href={link} target="_blank" rel={`${LINK_REL} nofollow`}>{cellText(value, 120)}</a>;
  return <>{cellText(value, 300)}</>;
}

export function DataGrid({ columns, rows, spec, onSort, offset = 0, highlight }: {
  columns: Column[];
  rows: GridRow[];
  spec: QuerySpec;
  onSort: (column: Column) => void;
  offset?: number;
  highlight?: string | null;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = (row: GridRow) => {
    const text = columns.map((c) => cellText(row.cells[c.key])).join("\t");
    void navigator.clipboard?.writeText(text).then(() => { setCopied(row.row_id ?? String(row.position)); setTimeout(() => setCopied(null), 1500); });
  };
  return (
    <div className="table-wrap" role="region" aria-label="Table" tabIndex={0}>
      <table className="table">
        <thead>
          <tr>
            <th scope="col" className="n">#</th>
            {columns.map((column) => {
              const sort = spec.sort.find((s) => s.column === column.key);
              return (
                <th key={column.key} scope="col" aria-sort={sort ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}>
                  {column.sortable ? <button type="button" onClick={() => onSort(column)}>{column.header}</button> : column.header}
                </th>
              );
            })}
            <th scope="col">{m.table.source}</th>
            <th scope="col"><span className="sr-only">{m.table.copy}</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const link = safeLink(row.source.url);
            const key = row.row_id ?? String(row.position);
            const hit = Boolean(highlight && (row.ref === highlight || row.source.code === highlight));
            return (
              <tr key={key} aria-selected={hit || undefined} className={hit ? "hit" : undefined}>
                <td className="n num">{offset + i + 1}</td>
                {columns.map((c) => <td key={c.key}><Cell column={c} value={row.cells[c.key]} /></td>)}
                <td>{link ? <a href={link} target="_blank" rel={`${LINK_REL} nofollow`}>{cellText(row.source.code) || "Open"}</a> : row.source.code ? <span>{cellText(row.source.code)}</span> : <span className="muted">{m.table.noSource}</span>}</td>
                <td><button type="button" className="link-btn small" onClick={() => copy(row)}>{copied === key ? m.table.copied : m.table.copy}</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}


