"use client";
// One account table (people, jobs, tech, facts): server-side sort, filter and search through the
// tables API, keyset paging, a Download as Excel link, and ?ref= highlights one row.
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { m } from "@/lib/messages";
import { cellText } from "@/lib/render";
import { addFilter, emptySpec, needsValue, removeFilter, tableUrl, toggleSort, withSearch, type Column, type Op, type QuerySpec } from "@/lib/query";
import { DataGrid, type GridRow } from "./DataGrid";
import { EmptyState, Loading, Notice } from "@/ui/primitives";

interface Page { run_id?: string; columns: Column[]; rows: GridRow[]; next_cursor: string | null; materialization: { status: string; reason: string | null } }
interface Pkg { package_id: string; status: string; files: { index: number; name: string }[] }

function opLabel(op: string): string {
  const text = op.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function TableScreen({ domain, table }: { domain: string; table: "people" | "jobs" | "tech" | "facts" }) {
  const params = useSearchParams();
  const highlight = params.get("ref");
  const [spec, setSpec] = useState<QuerySpec>(emptySpec());
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [fCol, setFCol] = useState("");
  const [fOp, setFOp] = useState<Op>("eq");
  const [fVal, setFVal] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const cursor = cursors[cursors.length - 1] ?? null;
  const loaded = useApi<Page>(tableUrl(domain, table, spec, cursor));
  const page = loaded.data;
  const columns = page?.columns ?? [];
  const column = columns.find((c) => c.key === fCol);
  const reset = (next: QuerySpec) => { setCursors([null]); setSpec(next); };
  const apply = (e: React.FormEvent) => {
    e.preventDefault();
    if (!column) return;
    try { reset(addFilter(spec, column, fOp, fVal)); setFVal(""); setError(null); } catch (err) { setError(err instanceof Error ? err.message : "Invalid filter"); }
  };
  const download = async () => {
    setDownloading(true);
    setError(null);
    try {
      const pkg = await api<Pkg>("POST", "/v1/exports", { domain, kind: "csv", table });
      const file = pkg.files[0];
      if (pkg.status === "delivered" && file) { const a = document.createElement("a"); a.href = `/v1/exports/${encodeURIComponent(pkg.package_id)}/files/${file.index}`; a.download = file.name; a.click(); }
      else setError(m.account.buildFailed);
    } catch {
      setError(m.account.buildFailed);
    } finally {
      setDownloading(false);
    }
  };
  if (loaded.error) {
    if (loaded.error.status === 409 || loaded.error.status === 404) return <EmptyState title={m.table.noRun} />;
    return <Notice kind="error">{m.state.errorTitle} {m.state.errorBody}</Notice>;
  }
  if (!page) return <Loading />;
  return (
    <div className="stack">
      <div className="form-row">
        <form className="form-row" onSubmit={(e) => { e.preventDefault(); reset(withSearch(spec, search)); }} role="search">
          <div className="field">
            <label htmlFor="search">{m.table.search}</label>
            <input id="search" className="input" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={200} />
          </div>
          <button type="submit" className="btn">{m.table.search}</button>
        </form>
        <form className="form-row" onSubmit={apply} aria-label={m.table.filter}>
          <div className="field">
            <label htmlFor="fcol">{m.table.filter}</label>
            <select id="fcol" className="select" value={fCol} onChange={(e) => { setFCol(e.target.value); const c = columns.find((x) => x.key === e.target.value); if (c?.operators[0]) setFOp(c.operators[0]); }}>
              <option value="">{m.table.column}</option>
              {columns.map((c) => <option key={c.key} value={c.key}>{c.header}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="fop">{m.table.operator}</label>
            <select id="fop" className="select" value={fOp} onChange={(e) => setFOp(e.target.value as Op)}>
              {column ? column.operators.map((op) => <option key={op} value={op}>{opLabel(op)}</option>) : <option value="eq">{m.table.operator}</option>}
            </select>
          </div>
          {needsValue(fOp) ? (
            <div className="field">
              <label htmlFor="fval">{m.table.value}</label>
              <input id="fval" className="input" value={fVal} onChange={(e) => setFVal(e.target.value)} maxLength={200} />
            </div>
          ) : null}
          <button type="submit" className="btn" disabled={!column}>{m.table.add}</button>
        </form>
        <span className="grow" />
        <button type="button" className="link-btn" disabled={downloading} onClick={() => void download()}>{m.table.download}</button>
      </div>
      {spec.filters.length ? (
        <ul className="row" aria-label="Filters">
          {spec.filters.map((f, i) => (
            <li key={`${f.column}-${i}`} className="row small">
              <span>{f.column} {opLabel(f.op).toLowerCase()} {Array.isArray(f.value) ? f.value.join(", ") : cellText(f.value)}</span>
              <button type="button" className="link-btn small" onClick={() => reset(removeFilter(spec, i))}>{m.table.remove}</button>
            </li>
          ))}
          <li><button type="button" className="link-btn small" onClick={() => reset(emptySpec())}>{m.table.clear}</button></li>
        </ul>
      ) : null}
      {error ? <p className="error-text small" role="alert">{error}</p> : null}
      {page.materialization.status === "withheld" ? <Notice>{m.table.withheld(cellText(page.materialization.reason))}</Notice> : null}
      {page.rows.length === 0 ? (
        <EmptyState title={spec.search || spec.filters.length ? m.table.emptyFiltered : m.table.empty} />
      ) : (
        <DataGrid columns={columns} rows={page.rows} spec={spec} onSort={(c) => reset(toggleSort(spec, c))} highlight={highlight} />
      )}
      <div className="table-foot">
        <span className="small muted">{m.table.rowsLabel(page.rows.length)}</span>
        <div className="actions">
          <button type="button" className="btn sm" disabled={cursors.length < 2} onClick={() => setCursors((c) => c.slice(0, -1))}>{m.table.previous}</button>
          <button type="button" className="btn sm" disabled={!page.next_cursor} onClick={() => setCursors((c) => [...c, page.next_cursor ?? null])}>{m.table.next}</button>
        </div>
      </div>
    </div>
  );
}
