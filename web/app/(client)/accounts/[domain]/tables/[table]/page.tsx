"use client";
// One client table: server-side filter, sort and search, saved views (save, rename, archive),
// keyset paging and the source panel. Every cell is a React text node; every link is checked
// (VW4). A row without a source shows that state instead of a link (VW2).
import { useState } from "react";
import { useParams } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { ChevronLeft, ChevronRight, ExternalLink, Search, X } from "lucide-react";
import { api, type Session } from "@/lib/api";
import { SignedIn } from "@/ui/SignedIn";
import { useApi } from "@/lib/useApi";
import { FeedbackForm, markUseful } from "@/ui/Feedback";
import { DeepDiveLinks, DIVES } from "@/components/ws/DeepDiveLinks";
import { Badge, Banner, EmptyState, Monogram } from "@/ui/primitives";
import { cellText, LINK_REL, safeLink } from "@/lib/render";
import { addFilter, emptySpec, needsValue, removeFilter, tableUrl, toggleSort, withSearch, type Column, type Op, type QuerySpec } from "@/lib/query";

interface Row { position: number; ref: string; row_id: string; cells: Record<string, string | number | null>; source: { code: string | null; url: string | null; state: string } }
interface Page { run_id?: string; columns: Column[]; rows: Row[]; next_cursor: string | null; materialization: { status: string; reason: string | null } }
interface SavedView { id: string; name: string; table: string; shared: boolean; version: number; spec: QuerySpec }

function opLabel(op: string): string {
  const text = op.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Name a view: used to save a new one and to rename the selected one. */
function NameDialog({ open, onOpenChange, title, initial, action, onSubmit }: {
  open: boolean; onOpenChange: (v: boolean) => void; title: string; initial: string; action: string; onSubmit: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState(initial);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog.Root open={open} onOpenChange={(v) => { if (v) setName(initial); onOpenChange(v); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog" aria-describedby={undefined}>
          <Dialog.Title>{title}</Dialog.Title>
          <form className="form gap-top" onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim() || busy) return;
            setBusy(true);
            void onSubmit(name.trim().slice(0, 80)).finally(() => setBusy(false));
          }}>
            <div className="field">
              <label htmlFor="view-name">View name</label>
              <input id="view-name" className="input" value={name} maxLength={80} autoFocus onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="actions">
              <Dialog.Close className="btn quiet" type="button">Cancel</Dialog.Close>
              <button className="btn primary" type="submit" disabled={!name.trim() || busy}>{action}</button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function TableView({ s, domain, table }: { s: Session; domain: string; table: string }) {
  const [spec, setSpec] = useState<QuerySpec>(emptySpec());
  const [viewId, setViewId] = useState<string | null>(null);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Row | null>(null);
  const [fCol, setFCol] = useState(""); const [fOp, setFOp] = useState<Op>("eq"); const [fVal, setFVal] = useState("");
  const [search, setSearch] = useState("");
  const [naming, setNaming] = useState<"save" | "rename" | null>(null);
  const cursor = cursors[cursors.length - 1] ?? null;
  const loaded = useApi<Page>(tableUrl(domain, table, spec, cursor, viewId));
  const page = loaded.data;
  const savedViews = useApi<{ views: SavedView[] }>(`/v1/views?table=${encodeURIComponent(table)}`);
  const views = savedViews.data?.views ?? [];
  const view = views.find((v) => v.id === viewId) ?? null;
  const tableError = loaded.error ? `This table is not available (${loaded.error.type}).` : null;
  const reset = (next: QuerySpec) => { setViewId(null); setCursors([null]); setSpec(next); };
  const columns = page?.columns ?? [];
  const column = columns.find((c) => c.key === fCol);
  const rows = page?.rows ?? [];
  const label = DIVES.find((d) => d.href === `tables/${table}`)?.label ?? (table === "coverage" ? "Coverage" : cellText(table));
  const canSave = s.role !== "viewer";
  const apply = (e: React.FormEvent) => {
    e.preventDefault();
    if (!column) return;
    try { reset(addFilter(spec, column, fOp, fVal)); setFVal(""); } catch (err) { setError(err instanceof Error ? err.message : "Invalid filter"); }
  };
  const save = async (name: string) => {
    setError(null);
    try {
      await api("POST", "/v1/views", { table, name, spec, shared: false });
      savedViews.reload(); setNaming(null); setNotice(`Saved ${name}.`);
    } catch { setError("The view could not be saved."); }
  };
  const rename = async (name: string) => {
    if (!view) return;
    setError(null);
    try {
      await api("PUT", `/v1/views/${encodeURIComponent(view.id)}`, { expected_version: view.version, name });
      savedViews.reload(); setNaming(null); setNotice(`Renamed to ${name}.`);
    } catch { setError("The view could not be renamed. It may have changed; reload and try again."); }
  };
  const archive = async () => {
    if (!view) return;
    setError(null);
    try {
      await api("POST", `/v1/views/${encodeURIComponent(view.id)}/archive`, { expected_version: view.version });
      setViewId(null); setCursors([null]); savedViews.reload(); setNotice(`Archived ${view.name}.`);
    } catch { setError("The view could not be archived."); }
  };
  return (
    <div className="wide">
      <div className="account-head">
        <Monogram name={domain} />
        <div>
          <h1>{label}</h1>
          <p className="sub">{domain}</p>
        </div>
      </div>
      <DeepDiveLinks domain={domain} />

      <div className="dive-toolbar">
        <div className="field">
          <label htmlFor="view">Saved view</label>
          <select id="view" className="select" value={viewId ?? ""} onChange={(e) => { setCursors([null]); setViewId(e.target.value || null); setNotice(null); }}>
            <option value="">None</option>
            {views.map((v) => <option key={v.id} value={v.id}>{cellText(v.name)}{v.shared ? " (shared)" : ""}</option>)}
          </select>
        </div>
        {canSave ? <button type="button" className="btn" onClick={() => setNaming("save")}>Save this view</button> : null}
        {canSave && view ? (
          <>
            <button type="button" className="btn quiet" onClick={() => setNaming("rename")}>Rename view</button>
            <button type="button" className="btn quiet" onClick={() => void archive()}>Archive view</button>
          </>
        ) : null}
        <form className="row grow end" onSubmit={(e) => { e.preventDefault(); reset(withSearch(spec, search)); }} role="search">
          <div className="field grow">
            <label htmlFor="search" className="sr-only">Search</label>
            <div className="search">
              <Search size={16} strokeWidth={1.7} aria-hidden="true" />
              <input id="search" className="input" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={200} placeholder="Search this table" />
            </div>
          </div>
          <button type="submit" className="btn">Search</button>
        </form>
      </div>

      <form className="form-row" onSubmit={apply} aria-label="Add a filter">
        <div className="field">
          <label htmlFor="fcol">Filter</label>
          <select id="fcol" className="select" value={fCol} onChange={(e) => { setFCol(e.target.value); const c = columns.find((x) => x.key === e.target.value); if (c?.operators[0]) setFOp(c.operators[0]); }}>
            <option value="">Column</option>
            {columns.map((c) => <option key={c.key} value={c.key}>{c.header}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="fop" className="sr-only">Operator</label>
          <select id="fop" aria-label="Operator" className="select" value={fOp} onChange={(e) => setFOp(e.target.value as Op)}>
            {column ? column.operators.map((op) => <option key={op} value={op}>{opLabel(op)}</option>) : <option value="eq">Operator</option>}
          </select>
        </div>
        {needsValue(fOp) ? (
          <div className="field">
            <label htmlFor="fval" className="sr-only">Value</label>
            <input id="fval" aria-label="Value" className="input" placeholder="Value" value={fVal} onChange={(e) => setFVal(e.target.value)} maxLength={200} />
          </div>
        ) : null}
        <button type="submit" className="btn tinted" disabled={!column}>Add filter</button>
      </form>

      {spec.filters.length ? (
        <ul className="row gap-top" aria-label="Filters">{spec.filters.map((f, i) => (
          <li key={`${f.column}-${i}`} className="chip">
            {f.column} {opLabel(f.op).toLowerCase()} {Array.isArray(f.value) ? f.value.join(", ") : cellText(f.value)}
            <button type="button" className="icon-btn" aria-label="Remove filter" onClick={() => reset(removeFilter(spec, i))}><X size={14} strokeWidth={1.8} /></button>
          </li>
        ))}</ul>
      ) : null}

      <div className="stack gap-top">
        {error ?? tableError ? <Banner kind="error">{error ?? tableError}</Banner> : null}
        {notice ? <p className="inline-status" role="status">{notice}</p> : null}
        {page?.materialization.status === "withheld" ? <Banner kind="warn" role="status">These tables are withheld for this run ({cellText(page.materialization.reason)}).</Banner> : null}
      </div>

      <div className="dive-layout gap-top" data-panel={selected !== null}>
        <div className="stack">
          <div className="table-wrap">
            <table className="table">
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
                {rows.map((r) => (
                  <tr key={r.position} aria-selected={selected?.position === r.position || undefined}>
                    {columns.map((c) => <td key={c.key}>{cellText(r.cells[c.key])}</td>)}
                    <td>{r.source.state === "linked" ? <button type="button" className="chip" onClick={() => setSelected(r)}>{cellText(r.source.code)}</button> : <span className="ink-2">No source</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {page && rows.length === 0 && !loaded.error ? (
            <EmptyState title="No rows" body={spec.search || spec.filters.length ? "Nothing matches this search or these filters." : "This table has no rows for the latest research run."} />
          ) : null}
          <div className="table-foot">
            <button type="button" className="btn sm" disabled={cursors.length < 2} onClick={() => setCursors((c) => c.slice(0, -1))}><ChevronLeft size={16} aria-hidden="true" />Previous</button>
            <button type="button" className="btn sm" disabled={!page?.next_cursor} onClick={() => setCursors((c) => [...c, page?.next_cursor ?? null])}>Next<ChevronRight size={16} aria-hidden="true" /></button>
          </div>
        </div>
        {selected ? (
          <aside className="source-panel evidence stack" aria-label="Source">
            <div className="row between">
              <h2 className="t-headline">Source {cellText(selected.source.code)}</h2>
              <button type="button" className="icon-btn" aria-label="Close" onClick={() => setSelected(null)}><X size={18} strokeWidth={1.7} /></button>
            </div>
            <p className="ink-2">Row <Badge>{cellText(selected.ref)}</Badge></p>
            {safeLink(selected.source.url) ? (
              <p><a className="open" href={safeLink(selected.source.url) ?? undefined} target="_blank" rel={`${LINK_REL} nofollow`} onClick={() => { if (table === "facts") markUseful("confirmed_fact_referenced", selected.row_id, "opened"); }}>Open the source <ExternalLink size={14} aria-hidden="true" /></a></p>
            ) : <p className="archive">Archive record</p>}
            {table === "facts" ? (
              <div><button type="button" className="btn sm" onClick={() => { void navigator.clipboard?.writeText(Object.values(selected.cells).map((v) => cellText(v)).join(" | ")); markUseful("confirmed_fact_referenced", selected.row_id, "noted"); }}>Copy into a note</button></div>
            ) : null}
            {page?.run_id ? <FeedbackForm label={`Feedback on row ${selected.ref}`} target={{ domain, runId: page.run_id, table, rowId: selected.row_id }} /> : null}
          </aside>
        ) : null}
      </div>

      <NameDialog open={naming === "save"} onOpenChange={(v) => setNaming(v ? "save" : null)} title="Save this view" initial="" action="Save view" onSubmit={save} />
      <NameDialog open={naming === "rename"} onOpenChange={(v) => setNaming(v ? "rename" : null)} title="Rename view" initial={view?.name ?? ""} action="Save name" onSubmit={rename} />
    </div>
  );
}

export default function TablePage() {
  const params = useParams<{ domain: string; table: string }>();
  return <SignedIn>{(s) => <TableView s={s} domain={decodeURIComponent(params.domain)} table={params.table} />}</SignedIn>;
}
