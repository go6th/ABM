// One sheet of a delivered workbook as the file prints it: its title, notes and paragraphs, then
// each table (header and rows, links kept, formulas already computed by the server). Each table
// sits in its own frame that scrolls sideways, so a wide sheet never widens the page.
import type { SheetTab } from "@/lib/gen/api.gen";
import { LINK_REL, safeLink } from "@/lib/render";

type Block = SheetTab["blocks"][number];
type Cell = NonNullable<Block["rows"]>[number][number];

function Lines({ lines, first }: { lines: string[]; first: boolean }) {
  return (
    <div className="tab-lines">
      {lines.map((l, i) => <p key={i} className={first && i === 0 ? "first" : undefined}>{l}</p>)}
    </div>
  );
}

function text(cell: Cell | null | undefined): string {
  const t = cell?.text;
  return typeof t === "string" ? t : t == null ? "" : String(t);
}

function Table({ block, label }: { block: Block; label: string }) {
  const header = block.header ?? [];
  const rows = (block.rows ?? []).map((r) => (Array.isArray(r) ? r : []));
  // a row longer than the header still shows every cell under an empty heading
  const width = Math.max(header.length, ...rows.map((r) => r.length), 0);
  const heads = Array.from({ length: width }, (_, j) => header[j] ?? "");
  return (
    <div className="tab-table">
      <div className="xl-frame" tabIndex={0} role="region" aria-label={label}>
        <table className="xl">
          <caption className="sr-only">{label}, {rows.length} {rows.length === 1 ? "row" : "rows"}.</caption>
          <thead>
            <tr>
              <th className="rn" scope="col"><span className="sr-only">Row</span></th>
              {heads.map((h, j) => <th key={j} scope="col"><span className="th">{h}</span></th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="rn">{i + 1}</td>
                {heads.map((_, j) => {
                  const c = r[j];
                  const value = text(c);
                  const href = safeLink(c?.href ?? "");
                  const long = value.length > 80;
                  return (
                    <td key={j} className={long ? "wrap" : undefined}>
                      {href ? <a className="src-link" href={href} target="_blank" rel={LINK_REL}>{value || "Open"}</a> : value}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length === 0 ? <div className="state"><p>No rows in this table of the file.</p></div> : null}
      {block.cut ? <div className="tab-lines"><p className="fine">{block.cut} more rows are in the file. Download the file for all of them.</p></div> : null}
    </div>
  );
}

export function TabView({ tab }: { tab: SheetTab }) {
  // consecutive text lines form one paragraph group; the first group opens with the title
  const groups: Array<{ kind: "text"; lines: string[] } | { kind: "table"; block: Block }> = [];
  for (const b of tab.blocks ?? []) {
    if (!b) continue;
    const last = groups[groups.length - 1];
    if (b.kind === "text") {
      if (last && last.kind === "text") last.lines.push(b.text ?? "");
      else groups.push({ kind: "text", lines: [b.text ?? ""] });
    } else {
      groups.push({ kind: "table", block: b });
    }
  }
  let tables = 0;
  return (
    <div className="card clip">
      <div className="xl-caption"><span className="sheet" aria-hidden="true" />{tab.file} · {tab.tab_label}</div>
      {groups.length ? groups.map((g, i) => {
        if (g.kind === "text") return <Lines key={i} lines={g.lines} first={i === 0} />;
        tables += 1;
        return <Table key={i} block={g.block} label={`${tab.tab_label} table ${tables}`} />;
      }) : <div className="state"><h2>This sheet is empty</h2></div>}
    </div>
  );
}
