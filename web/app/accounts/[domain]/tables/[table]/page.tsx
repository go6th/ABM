"use client";
// One client table: server-side filter, sort and search, saved views, keyset paging and the
// source drawer. Every cell is a React text node; every link is checked (VW4). A row without a
// source shows that state instead of a link (VW2).
import { useState } from "react";
import { useParams } from "next/navigation";
import { api, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { useApi } from "@/lib/useApi";
import { FeedbackForm, markUseful } from "@/components/Feedback";
import { cellText, LINK_REL, safeLink } from "@/lib/render";
import { addFilter, emptySpec, needsValue, removeFilter, tableUrl, toggleSort, withSearch, type Column, type Op, type QuerySpec } from "@/lib/query";

interface Row { position: number; ref: string; row_id: string; cells: Record<string, string | number | null>; source: { code: string | null; url: string | null; state: string } }
interface Page { run_id?: string; columns: Column[]; rows: Row[]; next_cursor: string | null; materialization: { status: string; reason: string | null } }
interface SavedView { id: string; name: string; table: string; shared: boolean; version: number; spec: QuerySpec }

function TableView({ s, domain, table }: { s: Session; domain: string; table: string }) {
  const [spec, setSpec] = useState<QuerySpec>(emptySpec());
  const [viewId, setViewId] = useState<string | null>(null);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Row | null>(null);
  const [fCol, setFCol] = useState(""); const [fOp, setFOp] = useState<Op>("eq"); const [fVal, setFVal] = useState("");
  const [search, setSearch] = useState("");
  const cursor = cursors[cursors.length - 1] ?? null;
  const loaded = useApi<Page>(tableUrl(domain, table, spec, cursor, viewId));
  const page = loaded.data;
  const savedViews = useApi<{ views: SavedView[] }>(`/v1/views?table=${encodeURIComponent(table)}`);
  const views = savedViews.data?.views ?? [];
  const tableError = loaded.error ? `This table is not available (${loaded.error.type}).` : null;
  const reset = (next: QuerySpec) => { setViewId(null); setCursors([null]); setSpec(next); };
  const columns = page?.columns ?? [];
  const column = columns.find((c) => c.key === fCol);
  const apply = (e: React.FormEvent) => {
    e.preventDefault();
    if (!column) return;
    try { reset(addFilter(spec, column, fOp, fVal)); setFVal(""); } catch (err) { setError(err instanceof Error ? err.message : "Invalid filter"); }
  };
  const save = async () => {
    const name = window.prompt("Name this view");
    if (!name) return;
    try { await api("POST", "/v1/views", { table, name: name.slice(0, 80), spec, shared: false }); savedViews.reload(); }
    catch { setError("The view could not be saved."); }
  };
  return (
    <section>
      <h1>{cellText(table)} for {domain}</h1>
      <div className="row">
        <label htmlFor="view">Saved view</label>
        <select id="view" value={viewId ?? ""} onChange={(e) => { setCursors([null]); setViewId(e.target.value || null); }}>
          <option value="">None</option>
          {views.map((v) => <option key={v.id} value={v.id}>{cellText(v.name)}{v.shared ? " (shared)" : ""}</option>)}
        </select>
        {s.role !== "viewer" ? <button type="button" onClick={() => void save()}>Save this view</button> : null}
      </div>
      <form className="row" onSubmit={(e) => { e.preventDefault(); reset(withSearch(spec, search)); }} role="search">
        <label htmlFor="search">Search</label>
        <input id="search" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={200} />
        <button type="submit">Search</button>
      </form>
      <form className="row" onSubmit={apply} aria-label="Add a filter">
        <label htmlFor="fcol">Filter</label>
        <select id="fcol" value={fCol} onChange={(e) => { setFCol(e.target.value); const c = columns.find((x) => x.key === e.target.value); if (c?.operators[0]) setFOp(c.operators[0]); }}>
          <option value="">Column</option>
          {columns.map((c) => <option key={c.key} value={c.key}>{c.header}</option>)}
        </select>
        <select aria-label="Operator" value={fOp} onChange={(e) => setFOp(e.target.value as Op)}>
          {(column?.operators ?? []).map((op) => <option key={op} value={op}>{op.replaceAll("_", " ")}</option>)}
        </select>
        {needsValue(fOp) ? <input aria-label="Value" value={fVal} onChange={(e) => setFVal(e.target.value)} maxLength={200} /> : null}
        <button type="submit" disabled={!column}>Add filter</button>
      </form>
      {spec.filters.length ? (
        <ul className="row">{spec.filters.map((f, i) => (
          <li key={`${f.column}-${i}`} className="badge">{f.column} {f.op} {Array.isArray(f.value) ? f.value.join(", ") : cellText(f.value)}
            <button type="button" aria-label="Remove filter" onClick={() => reset(removeFilter(spec, i))}>x</button></li>
        ))}</ul>
      ) : null}
      {error ?? tableError ? <p className="error" role="alert">{error ?? tableError}</p> : null}
      {page?.materialization.status === "withheld" ? <p role="status">These tables are withheld for this run ({cellText(page.materialization.reason)}).</p> : null}
      <table className="data">
        <thead><tr>
          {columns.map((c) => {
            const sort = spec.sort.find((x) => x.column === c.key);
            return (
              <th key={c.key} scope="col" aria-sort={sort ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}>
                {c.sortable ? <button type="button" onClick={() => reset(toggleSort(spec, c))}>{c.header}</button> : c.header}
              </th>
            );
          })}
          <th scope="col">Source</th>
        </tr></thead>
        <tbody>
          {(page?.rows ?? []).map((r) => (
            <tr key={r.position} className={selected?.position === r.position ? "selected" : undefined}>
              {columns.map((c) => <td key={c.key}>{cellText(r.cells[c.key])}</td>)}
              <td>{r.source.state === "linked" ? <button type="button" onClick={() => setSelected(r)}>{cellText(r.source.code)}</button> : <span className="muted">No source</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <button type="button" disabled={cursors.length < 2} onClick={() => setCursors((c) => c.slice(0, -1))}>Previous</button>
        <button type="button" disabled={!page?.next_cursor} onClick={() => setCursors((c) => [...c, page?.next_cursor ?? null])}>Next</button>
      </div>
      {selected ? (
        <aside className="drawer" aria-label="Source">
          <h2>Source {cellText(selected.source.code)}</h2>
          <p>Row {cellText(selected.ref)}</p>
          {safeLink(selected.source.url) ? (
            <p><a href={safeLink(selected.source.url) ?? undefined} target="_blank" rel={LINK_REL} onClick={() => { if (table === "facts") markUseful("confirmed_fact_referenced", selected.row_id, "opened"); }}>Open the source</a></p>
          ) : <p className="muted">No link</p>}
          {table === "facts" ? (
            <button type="button" onClick={() => { void navigator.clipboard?.writeText(Object.values(selected.cells).map((v) => cellText(v)).join(" | ")); markUseful("confirmed_fact_referenced", selected.row_id, "noted"); }}>Copy into a note</button>
          ) : null}
          {page?.run_id ? <FeedbackForm label={`Feedback on row ${selected.ref}`} target={{ domain, runId: page.run_id, table, rowId: selected.row_id }} /> : null}
          <button type="button" onClick={() => setSelected(null)}>Close</button>
        </aside>
      ) : null}
    </section>
  );
}

export default function TablePage() {
  const params = useParams<{ domain: string; table: string }>();
  return <SignedIn>{(s) => <TableView s={s} domain={decodeURIComponent(params.domain)} table={params.table} />}</SignedIn>;
}
