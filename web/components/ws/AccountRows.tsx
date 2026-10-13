"use client";
// The accounts table. Each row opens the account; the schedule column reads the browser-kept
// schedule choice until the scheduling service exists.
import { useRouter } from "next/navigation";
import Link from "next/link";
import { m } from "@/lib/messages";
import { useSchedule } from "@/lib/schedule";
import { Status, type StatusTone } from "@/ui/primitives";

export interface AccountRow { domain: string; name: string; researched: string; tone: StatusTone; signals: number }

function ScheduleCell({ domain }: { domain: string }) {
  const schedule = useSchedule(domain);
  return <>{m.account.cadence[schedule.cadence]}</>;
}

export function AccountRows({ rows }: { rows: AccountRow[] }) {
  const router = useRouter();
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">{m.accounts.account}</th>
            <th scope="col">{m.accounts.lastResearched}</th>
            <th scope="col">{m.accounts.schedule}</th>
            <th scope="col">{m.accounts.signals}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const href = `/accounts/${encodeURIComponent(r.domain)}`;
            return (
              <tr key={r.domain} className="row-link" onClick={() => router.push(href)}>
                <td><Link href={href} onClick={(e) => e.stopPropagation()}>{r.name}</Link>{r.name !== r.domain ? <span className="sub">{r.domain}</span> : null}</td>
                <td><Status tone={r.tone}>{r.researched}</Status></td>
                <td><ScheduleCell domain={r.domain} /></td>
                <td>{r.signals > 0 ? <Status tone="ok" plain>{m.accounts.signalsCount(r.signals)}</Status> : <span className="muted">{m.accounts.noSignals}</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
