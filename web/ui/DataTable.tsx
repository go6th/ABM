// A data table with its own loading, error and empty rows. The region scrolls horizontally on
// narrow screens and is keyboard-focusable so it can be scrolled without a pointer.
import type { ReactNode } from "react";

export interface DataColumn<Row> {
  key: string;
  header: ReactNode;
  render: (row: Row) => ReactNode;
  numeric?: boolean;
}

export function DataTable<Row>({
  caption,
  columns,
  rows,
  rowKey,
  loading = false,
  error,
  emptyMessage = "No records to show.",
}: {
  caption: string;
  columns: readonly DataColumn<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  loading?: boolean;
  error?: string;
  emptyMessage?: string;
}) {
  const stateMessage = loading ? "Loading records" : error ?? emptyMessage;
  return (
    <div className="table-wrap" role="region" aria-label={caption} tabIndex={0}>
      <table className="table" aria-busy={loading}>
        <caption className="sr-only">{caption}</caption>
        <thead><tr>{columns.map((column) => <th key={column.key} scope="col" className={column.numeric ? "num" : undefined}>{column.header}</th>)}</tr></thead>
        <tbody>
          {loading || error || rows.length === 0 ? (
            <tr><td className="ink-2" colSpan={Math.max(columns.length, 1)} role={error ? "alert" : loading ? "status" : undefined}>{stateMessage}</td></tr>
          ) : rows.map((row) => (
            <tr key={rowKey(row)}>{columns.map((column) => <td key={column.key} className={column.numeric ? "num" : undefined}>{column.render(row)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
