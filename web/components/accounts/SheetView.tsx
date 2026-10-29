"use client";
// A deep-dive table as the design draws it: filter chips with counts, Expand or Shrink, Download
// CSV and Open in Excel (every row under the chosen filter and sort, never cut), then the spreadsheet (caption, sticky header, row numbers, sortable headings with
// arrows). Chips and headings are links, so filtering and sorting always run on the server.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Sheet, SheetCellText } from "@/lib/gen/api.gen";
import { LINK_REL, safeLink } from "@/lib/render";
import { Grade, Pill, type PillTone } from "@/ui/design";
import { fillClass, tdClass } from "./cell-fill";

type Row = Sheet["rows"][number];
type Column = Sheet["columns"][number];

// Coverage tracker statuses: Done is good, Partial and None found are worth a look, Not done and
// Blocked are gaps.
const STATUS_TONE: Record<string, PillTone> = {
  Done: "ok", Partial: "warn", "None found": "neutral", "Not done": "bad", Blocked: "bad", "Not checked": "neutral",
};

// The archived copy behind a link (the research keeps what it read): a same-origin address the
// API serves as a readable page, so a link that has died still opens the captured text.
function Archived({ row, k }: { row: Row; k: string }) {
  const found = row.archives?.[k];
  if (!found || !found.href.startsWith("/v1/")) return null;
  const when = found.archived_at.slice(0, 10);
  return <a className="arc-link" href={found.href} target="_blank" rel={LINK_REL} title={`Archived copy, captured ${when}`}>archived</a>;
}

// The whole cell behind one the page shows cut (the API caps a cell's text on a page): Expand
// fetches it from the cell route under the page's own filter and sort.
function More({ row, col, length, cellHref, onText }: { row: Row; col: Column; length: number; cellHref: string; onText: (t: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const load = async () => {
    setBusy(true);
    setFailed(false);
    try {
      const q = new URLSearchParams({ n: String(row.n), col: col.key });
      const found = await api<SheetCellText>("GET", `${cellHref}${cellHref.includes("?") ? "&" : "?"}${q.toString()}`);
      onText(found.text ?? "");
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button type="button" className="cell-more" onClick={load} disabled={busy} aria-label={`Show the whole ${col.label} cell of row ${row.n}`}>
        {busy ? "Loading…" : `Show all (${length.toLocaleString()} characters)`}
      </button>
      {failed ? <span className="cell-note" role="alert">The whole cell could not be loaded. It is in the downloaded file.</span> : null}
    </>
  );
}

function Cell({ col, row, cellHref }: { col: Column; row: Row; cellHref: string }) {
  const raw = row.cells?.[col.key];
  const shown = typeof raw === "string" ? raw : raw == null ? "" : String(raw);
  const [full, setFull] = useState<string | null>(null);
  const value = full ?? shown;
  const fill = fillClass(row.fills?.[col.key]);
  const cutTo = row.cut?.[col.key];
  const cut = typeof cutTo === "number" && cutTo > 0 && full === null ? cutTo : null;
  if (col.kind === "url") {
    // a link column of the delivered file: the file's own text ("Open profile", "Open posting",
    // or the address) opening the address the file links to, and the archived copy beside it
    const href = safeLink(row.links?.[col.key] ?? "");
    return <td className={fill ?? undefined}>{href ? <><a className="src-link" href={href} target="_blank" rel={LINK_REL}>{value || "Open"}</a><Archived row={row} k={col.key} /></> : value}</td>;
  }
  if (col.kind === "code") {
    // the file's source code, opening the source it names when the registry links it
    const href = safeLink(row.link ?? "");
    return <td className={fill ?? undefined}>{href && value ? <><a className="src-link" href={href} target="_blank" rel={LINK_REL}>{value}</a><Archived row={row} k="link" /></> : value}</td>;
  }
  if (col.kind === "link") {
    // "6th / LinkedIn": who found it and the site, opening the original page (never a connector)
    const href = safeLink(row.link ?? "");
    if (href) return <td className={fill ?? undefined}><a className="src-link" href={href} target="_blank" rel={LINK_REL}>{row.link_label ?? "Open"}</a><Archived row={row} k="link" /></td>;
    return <td className={fill ?? undefined}>{row.link_label ? <span className="mut">{row.link_label}</span> : null}</td>;
  }
  if (col.kind === "grade") return <td className={fill ?? undefined}>{value ? <Grade grade={value} /> : null}</td>;
  if (col.kind === "priority") {
    const tone = value === "A" ? "run" : "neutral";
    return <td className={tdClass("c", fill)}>{value ? <Pill tone={tone} small>{value}</Pill> : null}</td>;
  }
  if (col.kind === "status") {
    const tone = STATUS_TONE[value] ?? "neutral";
    return <td className={fill ?? undefined}>{value ? <Pill tone={tone} small>{value}</Pill> : null}</td>;
  }
  if (col.kind === "strong") return <td className={fill ?? undefined}><b>{value}</b></td>;
  if (col.kind === "num") return <td className={tdClass("c", fill)}>{value}</td>;
  if (cut !== null || col.kind === "long" || col.key === "snippet" || col.key === "reason" || value.length > 120) {
    // long text reads four lines; Expand shows it whole; a cell cut by the page fetches the rest
    return (
      <td className={tdClass("wrap", fill)}>
        <div className="clamp">{value}</div>
        {cut !== null ? <More row={row} col={col} length={cut} cellHref={cellHref} onText={setFull} /> : null}
      </td>
    );
  }
  return <td className={fill ?? undefined}>{value}</td>;
}

// The caption's sort phrase; a delivered file's table opens in the file's own row order.
function sortedBy(sheet: Sheet): string {
  const col = (sheet.columns ?? []).find((c) => c.key === sheet.sort);
  if (!col) return sheet.from_file ? "In the file's order." : "";
  return `Sorted by ${col.label} ${sheet.dir === "asc" ? "ascending" : "descending"}.`;
}

/** One whole cell of the table (the page cuts long cells), under the page's filter and sort. */
export function cellHref(domain: string, sheet: Pick<Sheet, "key" | "filter" | "sort" | "dir">): string {
  const q = new URLSearchParams({ filter: sheet.filter ?? "", sort: sheet.sort ?? "", dir: sheet.dir ?? "" });
  return `/v1/accounts/${encodeURIComponent(domain)}/sheets/${encodeURIComponent(sheet.key)}/cell?${q.toString()}`;
}

/** The table's own CSV or Excel download (every row under the chosen filter and sort). */
export function exportHref(domain: string, sheet: Pick<Sheet, "key" | "filter" | "sort" | "dir">, format: "csv" | "xlsx"): string {
  const q = new URLSearchParams({ filter: sheet.filter ?? "", sort: sheet.sort ?? "", dir: sheet.dir ?? "" });
  return `/v1/accounts/${encodeURIComponent(domain)}/sheets/${encodeURIComponent(sheet.key)}/${format}?${q.toString()}`;
}

export function SheetView({ sheet, domain, base, moreHref }: { sheet: Sheet; domain: string; base: string; moreHref: string | null }) {
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
    // the frame also changes width without a window resize (Expand, a sidebar, fonts loading)
    const el = frame.current;
    const watch = el && typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => measure()) : null;
    if (el && watch) watch.observe(el);
    return () => {
      window.removeEventListener("resize", measure);
      watch?.disconnect();
    };
  }, [measure, sheet, big]);
  // a new page, filter or sort opens at its first row (the frame keeps its scroll otherwise;
  // the sideways scroll stays, so a heading sorted at the right stays in view)
  const view = `${sheet.filter}|${sheet.sort}|${sheet.dir}|${sheet.offset}`;
  useEffect(() => {
    const el = frame.current;
    if (el) el.scrollTop = 0;
  }, [view]);
  const columns = sheet.columns ?? [];
  const rows = sheet.rows ?? [];
  const filters = sheet.filters ?? [];
  const query = (change: Record<string, string>) => {
    const q = new URLSearchParams({ filter: sheet.filter ?? "", sort: sheet.sort ?? "", dir: sheet.dir ?? "", ...change });
    return `${base}?${q.toString()}`;
  };
  // every row of the table, whatever the filter (a table without an "all" chip counts its total)
  const all = filters.find((f) => f.key === "all")?.count ?? sheet.total ?? 0;
  const cell = cellHref(domain, sheet);
  return (
    <>
      <div className="sheet-bar">
        {filters.map((f) => (
          <Link key={f.key} className="fc" href={query({ filter: f.key })} aria-current={sheet.filter === f.key ? "true" : undefined} prefetch={false} scroll={false}>
            {f.key === "all" ? `All ${f.count}` : `${f.label} · ${f.count}`}
          </Link>
        ))}
        <span className="spacer" />
        {/* the label says what a click does, so no pressed state on top of it */}
        <button type="button" className="btn sm" onClick={() => setBig((v) => !v)} disabled={!rows.length}>
          {big ? "Shrink" : "Expand"}
        </button>
        {all ? <a className="btn sm" href={exportHref(domain, sheet, "csv")} download>Download CSV</a> : <span className="btn sm" aria-disabled="true">Download CSV</span>}
        {all ? <a className="btn primary sm" href={exportHref(domain, sheet, "xlsx")} download>Open in Excel</a> : <span className="btn primary sm" aria-disabled="true">Open in Excel</span>}
      </div>
      <div className="card clip">
        <div className="xl-caption"><span className="sheet" aria-hidden="true" />{sheet.file} · {sheet.sheet_name ?? "Sheet 1"} · {sheet.total} rows</div>
        {rows.length ? (
          <div className="xl-hint" data-show={moreRight} aria-hidden={!moreRight}>
            More columns to the right. Scroll the table sideways <span aria-hidden="true">→</span>
          </div>
        ) : null}
        {rows.length ? (
          <div ref={frame} onScroll={measure} className={big ? "xl-frame big" : "xl-frame"} tabIndex={0} role="region" aria-label={`${sheet.title} table`}>
            <table className="xl">
              <caption className="sr-only">{sheet.title}, {sheet.total} rows. {sortedBy(sheet)}</caption>
              <thead>
                <tr>
                  <th className="rn" scope="col"><span className="sr-only">Row</span></th>
                  {columns.map((c) => {
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
                {rows.map((r, i) => (
                  <tr key={`${r.n}-${i}`}>
                    <td className="rn">{r.n}</td>
                    {columns.map((c) => <Cell key={c.key} col={c} row={r} cellHref={cell} />)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {rows.length ? null : (
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
