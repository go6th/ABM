"use client";
// Research runs: one page of runs from the API with search and a status filter applied to that
// page. Status and label come from the API as they are; nothing is inferred.
import { Activity, ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { DataTable, type DataColumn } from "@/ui/DataTable";
import { Input } from "@/ui/Input";
import { SignedIn } from "@/ui/SignedIn";
import { Badge, EmptyState, LoadError, Monogram, PageHead } from "@/ui/primitives";
import type { RunListItem, RunPage } from "@/lib/gen/api.gen";
import { cellText } from "@/lib/render";
import { runState } from "@/lib/ws-format";
import { useApi } from "@/lib/useApi";

function formattedDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unknown"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function StatusBadge({ run }: { run: RunListItem }) {
  const state = runState(run.status);
  const tone = state === "done" ? "mint" : state === "running" ? "peri" : state === "failed" ? "rose" : "butter";
  return <Badge tone={tone} dot>{cellText(run.label)}</Badge>;
}

function ResearchRunList() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<Array<string | null>>([]);
  const requestPath = useMemo(() => {
    const query = new URLSearchParams({ limit: "50" });
    if (cursor) query.set("cursor", cursor);
    return `/v1/runs?${query.toString()}`;
  }, [cursor]);
  const page = useApi<RunPage>(requestPath);
  const statuses = [...new Set(page.data?.runs.map((run) => run.status) ?? [])].sort();
  const filteredRuns = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (page.data?.runs ?? []).filter((run) => {
      const matchesQuery = !needle || `${run.domain} ${run.run_id} ${run.label}`.toLowerCase().includes(needle);
      return matchesQuery && (!statusFilter || run.status === statusFilter);
    });
  }, [page.data, search, statusFilter]);

  const columns: DataColumn<RunListItem>[] = [
    {
      key: "account",
      header: "Account",
      render: (run) => (
        <Link className="row nowrap" href={`/accounts/${encodeURIComponent(run.domain)}`}>
          <Monogram name={run.domain} />
          <span className="truncate"><span className="strong">{cellText(run.domain)}</span><br /><span className="t-caption ink-2 mono">{cellText(run.run_id)}</span></span>
        </Link>
      ),
    },
    { key: "status", header: "Status", render: (run) => <StatusBadge run={run} /> },
    { key: "started", header: "Started", render: (run) => <span className="nowrap">{formattedDate(run.created_at)}</span> },
    {
      key: "details",
      header: "Details",
      render: (run) => <Link className="btn sm" href={`/runs/${encodeURIComponent(run.run_id)}`}>Open run</Link>,
    },
  ];

  return (
    <div className="content">
      <PageHead title="Research runs" sub="Every research run in this workspace, with the state the service reports." />
      {page.error ? <LoadError reference={null} retryHref="/runs" /> : page.data?.runs.length === 0 && !cursor ? (
        <EmptyState
          title="No research activity yet"
          body="Refresh an account from the web to start a run. It appears here with its state."
          icon={<Activity size={20} strokeWidth={1.8} />}
          action={{ href: "/accounts", label: "Go to accounts" }}
        />
      ) : (
        <>
          <div className="form-row toolbar">
            <Input label="Search this page" type="search" value={search} placeholder="Account, run ID or label" onChange={(event) => setSearch(event.target.value)} />
            <div className="field">
              <label htmlFor="run-status">Status on this page</label>
              <select id="run-status" className="select" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="">All statuses</option>
                {statuses.map((status) => <option key={status} value={status}>{cellText(status)}</option>)}
              </select>
            </div>
          </div>
          <DataTable
            caption="Research runs"
            columns={columns}
            rows={filteredRuns}
            rowKey={(run) => run.run_id}
            loading={!page.data}
            emptyMessage="No runs on this page match the filters."
          />
          {page.data ? (
            <div className="table-foot" aria-label="Research run pages">
              <span>Search and status apply to this page of results.</span>
              <div className="row">
                <button
                  className="btn sm"
                  type="button"
                  disabled={history.length === 0}
                  onClick={() => {
                    const nextHistory = [...history];
                    const previous = nextHistory.pop() ?? null;
                    setHistory(nextHistory);
                    setCursor(previous);
                  }}
                >
                  <ChevronLeft size={16} aria-hidden="true" />Previous
                </button>
                <button
                  className="btn sm"
                  type="button"
                  disabled={!page.data.next_cursor}
                  onClick={() => {
                    if (!page.data?.next_cursor) return;
                    setHistory((items) => [...items, cursor]);
                    setCursor(page.data.next_cursor);
                  }}
                >
                  Next<ChevronRight size={16} aria-hidden="true" />
                </button>
              </div>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

export default function RunsPage() {
  return <SignedIn>{() => <ResearchRunList />}</SignedIn>;
}
