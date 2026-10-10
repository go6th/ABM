"use client";

import { Activity, ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { DataTable, EmptyState, Input, type DataColumn } from "@/design/components";
import { SignedIn } from "@/components/SignedIn";
import type { RunListItem, RunPage } from "@/lib/gen/api.gen";
import { cellText } from "@/lib/render";
import { useApi } from "@/lib/useApi";

function formattedDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
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
        <Link className="client-account-name" href={`/accounts/${encodeURIComponent(run.domain)}`}>
          <strong>{cellText(run.domain)}</strong>
          <span>{cellText(run.run_id)}</span>
        </Link>
      ),
    },
    { key: "status", header: "Status", render: (run) => cellText(run.label) },
    { key: "started", header: "Started", render: (run) => formattedDate(run.created_at) },
    {
      key: "details",
      header: "Details",
      render: (run) => (
        <Link className="client-row-link" href={`/runs/${encodeURIComponent(run.run_id)}`}>
          Open run <ArrowUpRight aria-hidden="true" />
        </Link>
      ),
    },
  ];

  return (
    <div className="client-dashboard">
      <section className="client-dashboard-heading">
        <div>
          <p className="eyebrow">RESEARCH ACTIVITY</p>
          <h1>Research runs</h1>
          <p className="secondary">Review actual run states and open the corresponding execution details.</p>
        </div>
      </section>

      <section className="client-accounts-panel" aria-label="Research run history">
        <div className="client-list-heading">
          <div>
            <h2>Run history</h2>
            <p className="secondary">The API status and label are shown without inferred progress or transitions.</p>
          </div>
          <div className="runs-filters">
            <Input
              label="Search this page"
              type="search"
              value={search}
              placeholder="Account, run ID, or label"
              onChange={(event) => setSearch(event.target.value)}
            />
            <label className="field">
              <span className="label">Status on this page</span>
              <select className="select" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="">All statuses</option>
                {statuses.map((status) => <option key={status} value={status}>{cellText(status)}</option>)}
              </select>
            </label>
          </div>
        </div>

        {page.error ? (
          <div className="client-data-state error-box" role="alert">
            <strong>Research runs could not be loaded</strong>
            <p>{page.error.detail}</p>
            <button className="btn" type="button" onClick={page.reload}>Try again</button>
          </div>
        ) : (
          <>
            {page.data?.runs.length === 0 ? (
              <EmptyState
                title="No research activity yet"
                description="Start a run from an account workspace to see its actual execution state here."
                icon={<Activity aria-hidden="true" />}
              />
            ) : (
              <DataTable
                caption="Research runs"
                columns={columns}
                rows={filteredRuns}
                rowKey={(run) => run.run_id}
                loading={!page.data}
                emptyMessage="No runs on this page match the filters."
              />
            )}
            {page.data ? (
              <div className="client-pagination" aria-label="Research run pages">
                <span className="muted">Filtering applies to this page of results.</span>
                <div className="design-swatch-row">
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
                    Previous
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
                    Next
                  </button>
                </div>
              </div>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
}

export default function RunsPage() {
  return <SignedIn>{() => <ResearchRunList />}</SignedIn>;
}
