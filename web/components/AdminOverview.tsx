"use client";

import Link from "next/link";
import {
  Activity,
  ArrowRight,
  Building2,
  ClipboardList,
  ShieldCheck,
  Users,
} from "lucide-react";
import { DataTable, type DataColumn } from "@/design/components";
import { cellText } from "@/lib/render";
import { useApi } from "@/lib/useApi";
import type {
  AccountPage,
  AuditPage,
  Members,
  RunPage,
} from "@/lib/gen/api.gen";

function dateLabel(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? cellText(value)
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function AdminOverview() {
  const members = useApi<Members>("/v1/users");
  const accounts = useApi<AccountPage>("/v1/accounts?limit=200");
  const runs = useApi<RunPage>("/v1/runs?limit=8");
  const audit = useApi<AuditPage>("/v1/audit?limit=8");
  const activeMembers = members.data?.members.filter((member) => member.status === "active").length;

  const auditColumns: DataColumn<AuditPage["events"][number]>[] = [
    {
      key: "at",
      header: "When",
      render: (event) => dateLabel(event.at),
    },
    {
      key: "actor",
      header: "Who",
      render: (event) => cellText(event.actor ?? event.actor_kind),
    },
    {
      key: "action",
      header: "Activity",
      render: (event) => cellText(event.action),
    },
    {
      key: "outcome",
      header: "Outcome",
      render: (event) => cellText(event.outcome),
    },
  ];

  return (
    <div className="client-dashboard admin-overview">
      <header className="client-dashboard-heading">
        <div>
          <p className="eyebrow">ACCOUNT INTELLIGENCE · WORKSPACE ADMIN</p>
          <h1>Administration overview</h1>
          <p className="secondary">
            Manage this workspace and keep track of its research activity.
          </p>
        </div>
      </header>

      <section className="admin-stat-grid" aria-label="Workspace administration summary">
        <article className="client-stat-card">
          <span className="client-stat-icon lavender"><Users aria-hidden="true" /></span>
          <div>
            <span className="client-stat-label">Team members</span>
            <strong>{members.error ? "—" : members.data?.members.length ?? "…"}</strong>
          </div>
          <span className="client-stat-foot">
            {members.data ? `${activeMembers ?? 0} active` : "Workspace membership"}
          </span>
        </article>
        <article className="client-stat-card">
          <span className="client-stat-icon sky"><Building2 aria-hidden="true" /></span>
          <div>
            <span className="client-stat-label">Accounts</span>
            <strong>{accounts.error ? "—" : accounts.data?.accounts.length ?? "…"}</strong>
          </div>
          <span className="client-stat-foot">
            {accounts.data?.next_after ? "Accounts on the first page" : "In this workspace"}
          </span>
        </article>
        <article className="client-stat-card">
          <span className="client-stat-icon mint"><Activity aria-hidden="true" /></span>
          <div>
            <span className="client-stat-label">Recent research runs</span>
            <strong>{runs.error ? "—" : runs.data?.runs.length ?? "…"}</strong>
          </div>
          <span className="client-stat-foot">Latest results returned by the API</span>
        </article>
        <article className="client-stat-card">
          <span className="client-stat-icon peach"><ClipboardList aria-hidden="true" /></span>
          <div>
            <span className="client-stat-label">Recent audit events</span>
            <strong>{audit.error ? "—" : audit.data?.events.length ?? "…"}</strong>
          </div>
          <span className="client-stat-foot">Latest events returned by the API</span>
        </article>
      </section>

      <section className="admin-quick-actions" aria-label="Administration shortcuts">
        <Link className="admin-action-card" href="/settings/users">
          <span className="client-stat-icon lavender"><Users aria-hidden="true" /></span>
          <span><strong>Team &amp; access</strong><small>Review workspace roles and membership</small></span>
          <ArrowRight aria-hidden="true" />
        </Link>
        <Link className="admin-action-card" href="/audit">
          <span className="client-stat-icon peach"><ClipboardList aria-hidden="true" /></span>
          <span><strong>Audit trail</strong><small>Review recorded workspace activity</small></span>
          <ArrowRight aria-hidden="true" />
        </Link>
        <Link className="admin-action-card" href="/accounts">
          <span className="client-stat-icon sky"><Building2 aria-hidden="true" /></span>
          <span><strong>Research accounts</strong><small>Open the workspace account directory</small></span>
          <ArrowRight aria-hidden="true" />
        </Link>
      </section>

      <section className="admin-identity-note" aria-label="Account provisioning information">
        <span className="client-stat-icon butter"><ShieldCheck aria-hidden="true" /></span>
        <div>
          <h2>Workspace access</h2>
          <p>
            This panel manages workspace membership and roles. Login creation and password
            resets are not available through the current workspace API; use your configured
            identity provider for account credentials.
          </p>
        </div>
      </section>

      <section className="client-accounts-panel" aria-labelledby="admin-members-title">
        <div className="client-list-heading">
          <div>
            <h2 id="admin-members-title">Team members</h2>
            <p className="secondary">Current memberships and their workspace access status.</p>
          </div>
          <Link className="client-row-link" href="/settings/users">
            Manage access <ArrowRight aria-hidden="true" />
          </Link>
        </div>
        {members.error ? (
          <div className="client-data-state error-box" role="alert">
            <strong>Team members could not be loaded</strong>
            <p>{members.error.detail}</p>
            <button className="btn" type="button" onClick={members.reload}>Try again</button>
          </div>
        ) : !members.data ? (
          <p className="client-loading" role="status">Loading team members…</p>
        ) : members.data.members.length === 0 ? (
          <p className="muted">No team members were returned for this workspace.</p>
        ) : (
          <div className="admin-member-list">
            {members.data.members.slice(0, 5).map((member) => (
              <div className="admin-member-row" key={member.user_id}>
                <span className="admin-member-avatar" aria-hidden="true">
                  {member.user_id.slice(0, 1).toUpperCase()}
                </span>
                <span className="admin-member-name">{cellText(member.user_id)}</span>
                <span className="badge tone-lavender">{cellText(member.role)}</span>
                <span className={`badge ${member.status === "active" ? "tone-mint" : "tone-slate"}`}>
                  {cellText(member.status)}
                </span>
              </div>
            ))}
            {members.data.members.length > 5 ? (
              <Link className="admin-member-more" href="/settings/users">
                View all {members.data.members.length} members
              </Link>
            ) : null}
          </div>
        )}
      </section>

      <section className="client-accounts-panel" aria-labelledby="admin-audit-title">
        <div className="client-list-heading">
          <div>
            <h2 id="admin-audit-title">Recent activity</h2>
            <p className="secondary">Latest audit events returned for this workspace.</p>
          </div>
          <Link className="client-row-link" href="/audit">
            Open audit trail <ArrowRight aria-hidden="true" />
          </Link>
        </div>
        {audit.error ? (
          <div className="client-data-state error-box" role="alert">
            <strong>Recent activity could not be loaded</strong>
            <p>{audit.error.detail}</p>
            <button className="btn" type="button" onClick={audit.reload}>Try again</button>
          </div>
        ) : !audit.data ? (
          <p className="client-loading" role="status">Loading recent activity…</p>
        ) : (
          <DataTable
            caption="Recent workspace audit events"
            columns={auditColumns}
            rows={audit.data.events.slice(0, 5)}
            rowKey={(event) => String(event.seq)}
            emptyMessage="No audit events have been returned."
          />
        )}
      </section>
    </div>
  );
}
