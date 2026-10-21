"use client";
// A deep-dive table as the design draws it: filter chips with counts, Expand or Shrink, Download
// CSV and Open in Excel (every row under the chosen filter and sort, never cut), then the spreadsheet (caption, sticky header, row numbers, sortable headings with
// arrows). Chips and headings are links, so filtering and sorting always run on the server.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Sheet } from "@/lib/gen/api.gen";
import { LINK_REL, safeLink } from "@/lib/render";
import { Grade, Pill, type PillTone } from "@/ui/design";

type Row = Sheet["rows"][number];
type Column = Sheet["columns"][number];

// Coverage tracker statuses: Done is good, Partial and None found are worth a look, Not done and
// Blocked are gaps.
const STATUS_TONE: Record<string, PillTone> = {
  Done: "ok", Partial: "warn", "None found": "neutral", "Not done": "bad", Blocked: "bad", "Not checked": "neutral",
};

function Cell({ col, row }: { col: Column; row: Row }) {
  const value = row.cells[col.key] ?? "";
  if (col.kind === "link") {
    // "6th / LinkedIn": who found it and the site, opening the original page (never a connector)
    const href = safeLink(row.link ?? "");
    if (href) return <td><a className="src-link" href={href} target="_blank" rel={LINK_REL}>{row.link_label ?? "Open"}</a></td>;
    return <td>{row.link_label ? <span className="mut">{row.link_label}</span> : null}</td>;
  }
  if (col.kind === "grade") return <td>{value ? <Grade grade={value} /> : null}</td>;
  if (col.kind === "priority") {
    const tone = value === "A" ? "run" : "neutral";
    return <td className="c">{value ? <Pill tone={tone} small>{value}</Pill> : null}</td>;
  }
  if (col.kind === "status") {
    const tone = STATUS_TONE[value] ?? "neutral";
    return <td>{value ? <Pill tone={tone} small>{value}</Pill> : null}</td>;
  }
  if (col.kind === "strong") return <td><b>{value}</b></td>;
  if (col.kind === "num") return <td className="c">{value}</td>;
  return <td className={col.key === "snippet" || col.key === "reason" ? "wrap" : undefined}>{value}</td>;
}

export function SheetView({ sheet, base, moreHref }: { sheet: Sheet; base: string; moreHref: string | null }) {
  const [big, setBig] = useState(false);
  const frame = useRef<HTMLDivElement>(null);
  const [moreRight, setMoreRight] = useState(false);
  // Show "scroll for more columns" while columns sit off to the right (phone width mostly).
  const measure = useCallback(() => {
    const el = frame.current;
    setMoreRight(!!el && el.scrollWidth - el.clientWidth - el.scrollLeft > 8);
  }, []);
  useEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure, sheet]);
  const query = (change: Record<string, string>) => {
    const q = new URLSearchParams({ filter: sheet.filter, sort: sheet.sort, dir: sheet.dir, ...change });
    return `${base}?${q.toString()}`;
  };
  const excel = `/v1/accounts/${encodeURIComponent(base.split("/")[2] ?? "")}/sheets/${sheet.key}/xlsx?${new URLSearchParams({ filter: sheet.filter, sort: sheet.sort, dir: sheet.dir }).toString()}`;
  const all = sheet.filters.find((f) => f.key === "all")?.count ?? 0;
  return (
    <>
      <div className="sheet-bar">
        {sheet.filters.map((f) => (
          <Link key={f.key} className="fc" href={query({ filter: f.key })} aria-current={sheet.filter === f.key ? "true" : undefined} prefetch={false} scroll={false}>
            {f.key === "all" ? `All ${f.count}` : `${f.label} · ${f.count}`}
          </Link>
        ))}
        <span className="spacer" />
        <button type="button" className="btn sm" aria-pressed={big} onClick={() => setBig((v) => !v)} disabled={!sheet.rows.length}>
          {big ? "Shrink" : "Expand"}
        </button>
        {all ? <a className="btn sm" href={excel.replace("/xlsx?", "/csv?")} download>Download CSV</a> : <span className="btn sm" aria-disabled="true">Download CSV</span>}
        {all ? <a className="btn primary sm" href={excel} download>Open in Excel</a> : <span className="btn primary sm" aria-disabled="true">Open in Excel</span>}
      </div>
      <div className="card clip">
        <div className="xl-caption"><span className="sheet" aria-hidden="true" />{sheet.file} · Sheet 1 · {sheet.total} rows</div>
        {sheet.rows.length ? (
          <div className="xl-hint" data-show={moreRight} aria-hidden={!moreRight}>
            More columns to the right. Scroll the table sideways <span aria-hidden="true">→</span>
          </div>
        ) : null}
        {sheet.rows.length ? (
          <div ref={frame} onScroll={measure} className={big ? "xl-frame big" : "xl-frame"} tabIndex={0} role="region" aria-label={`${sheet.title} table`}>
            <table className="xl">
              <caption className="sr-only">{sheet.title}, {sheet.total} rows. Sorted by {sheet.columns.find((c) => c.key === sheet.sort)?.label} {sheet.dir === "asc" ? "ascending" : "descending"}.</caption>
              <thead>
                <tr>
                  <th className="rn" scope="col"><span className="sr-only">Row</span></th>
                  {sheet.columns.map((c) => {
                    const on = sheet.sort === c.key;
                    const ariaSort = on ? (sheet.dir === "asc" ? "ascending" : "descending") : undefined;
                    return (
                      <th key={c.key} scope="col" aria-sort={ariaSort}>
                        {c.sortable ? (
                          <Link className="th" href={query({ sort: c.key, dir: on && sheet.dir === "asc" ? "desc" : "asc" })} prefetch={false} scroll={false}>
                            {c.label} <span aria-hidden="true">{on ? (sheet.dir === "asc" ? "▲" : "▼") : ""}</span>
                          </Link>
                        ) : (
                          <span className="th">{c.label}</span>
                        )}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {sheet.rows.map((r) => (
                  <tr key={r.n}>
                    <td className="rn">{r.n}</td>
                    {sheet.columns.map((c) => <Cell key={c.key} col={c} row={r} />)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {sheet.rows.length ? null : (
          <div className="state">
            <h2>{all ? "No rows match this filter" : "Nothing here yet"}</h2>
            <p>{all ? "Choose another filter above." : "Rows appear here when research finds them."}</p>
          </div>
        )}
      </div>
      {moreHref ? <Link className="btn sm more-rows" href={moreHref} prefetch={false} scroll={false}>Show more rows</Link> : null}
    </>
  );
}
