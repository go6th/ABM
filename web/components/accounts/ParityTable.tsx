"use client";
// One table of a delivered file, read page by page from the parity API in the file's own order
// and with the file's own cell text: the same columns, labels and sort as the file, grade,
// priority and rank chips in the file's fill colours, and the file's links (Open profile, Open
// posting, Profile). Jobs and People page on the server; a small table loads in one page.
import { useState } from "react";
import type { ParityCell, ParityColumn, ParityTablePage, ParityTableRef } from "@/lib/gen/api.gen";
import { LINK_REL, safeLink } from "@/lib/render";
import { PAGE_LIMIT_DEFAULT } from "@/lib/parity/tableSpecs";
import { useApi } from "@/lib/useApi";
import { Grade, Pill, type PillTone } from "@/ui/design";

const STATUS_TONE: Record<string, PillTone> = {
  Open: "ok", Closed: "neutral", Done: "ok", Partial: "warn", "None found": "neutral", "Not done": "bad", Blocked: "bad", "Not checked": "neutral",
};

function Chip({ col, text }: { col: ParityColumn; text: string }) {
  const fill = col.fills?.[text];
  if (!text) return null;
  return <span className={fill ? `pchip fill-${fill}` : "pchip"}>{text}</span>;
}

export function Cell({ col, cell }: { col: ParityColumn; cell: ParityCell }) {
  const text = cell.text;
  if (col.kind === "link") {
    const href = safeLink(cell.href ?? "");
    return <td>{href ? <a className="src-link" href={href} target="_blank" rel={LINK_REL}>{text}</a> : text}</td>;
  }
  if (col.kind === "priority" || col.kind === "rank") return <td className="c"><Chip col={col} text={text} /></td>;
  if (col.kind === "grade") return <td>{text ? <Grade grade={text} /> : null}</td>;
  if (col.kind === "status") return <td>{text ? <Pill tone={STATUS_TONE[text] ?? "neutral"} small>{text}</Pill> : null}</td>;
  if (col.kind === "num") return <td className="c">{text}</td>;
  if (col.kind === "long" || text.length > 120) return <td className="wrap"><div className="clamp">{text}</div></td>;
  if (cell.href) {
    const href = safeLink(cell.href);
    return <td>{href ? <a className="src-link" href={href} target="_blank" rel={LINK_REL}>{text}</a> : text}</td>;
  }
  return <td>{text}</td>;
}

export function tablePath(domain: string, version: number, id: string, offset: number, limit: number): string {
  const q = new URLSearchParams({ version: String(version), offset: String(offset), limit: String(limit) });
  return `/v1/accounts/${encodeURIComponent(domain)}/parity/tables/${encodeURIComponent(id)}?${q.toString()}`;
}

export function ParityTable({ domain, version, table }: { domain: string; version: number; table: ParityTableRef }) {
  const limit = PAGE_LIMIT_DEFAULT;
  const [offset, setOffset] = useState(0);
  const { data, error } = useApi<ParityTablePage>(table.total ? tablePath(domain, version, table.id, offset, limit) : null);
  const columns = data?.columns ?? table.columns;
  const rows = data?.rows ?? [];
  const pages = Math.max(1, Math.ceil(table.total / limit));
  const pageNo = Math.floor(offset / limit) + 1;
  return (
    <section className="card clip parity-table" data-table={table.id} data-total={table.total} aria-labelledby={`pt-${table.id}`}>
      <div className="xl-caption" id={`pt-${table.id}`}>
        <span className="sheet" aria-hidden="true" />
        {table.sheet ?? table.title} · {table.total} rows
      </div>
      <div className="xl-frame" tabIndex={0} role="region" aria-label={`${table.title} table`}>
        <table className="xl">
          <caption className="sr-only">{table.title}, {table.total} rows, in the file&apos;s order.</caption>
          <thead>
            <tr>
              <th className="rn" scope="col"><span className="sr-only">Row</span></th>
              {columns.map((c) => <th key={c.key} scope="col"><span className="th">{c.header}</span></th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={offset + i}>
                <td className="rn">{offset + i + 1}</td>
                {columns.map((c, j) => <Cell key={c.key} col={c} cell={r[j] ?? { text: "" }} />)}
              </tr>
            ))}
          </tbody>
        </table>
        {table.total === 0 ? <p className="mut small pad">No rows in the file.</p> : null}
        {table.total > 0 && !data && !error ? <p className="mut small pad">Loading rows…</p> : null}
        {error ? <p className="bad small pad" role="alert">The rows could not be loaded.</p> : null}
      </div>
      {pages > 1 ? (
        <nav className="pager pad" aria-label={`${table.title} pages`}>
          <button type="button" className="btn sm" onClick={() => setOffset(Math.max(0, offset - limit))} disabled={offset === 0}>Previous</button>
          <span className="mut small">Page {pageNo} of {pages}</span>
          <button type="button" className="btn sm" onClick={() => setOffset(offset + limit)} disabled={offset + limit >= table.total}>Next</button>
        </nav>
      ) : null}
    </section>
  );
}
