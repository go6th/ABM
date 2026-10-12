"use client";
// What changed: an account's expired signals (moved to history, never deleted, RF2).
import { useParams } from "next/navigation";
import { History as HistoryIcon } from "lucide-react";
import { SignedIn } from "@/ui/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import { day, humanize } from "@/lib/ws-format";
import { DeepDiveLinks } from "@/components/ws/DeepDiveLinks";
import { GradeMark, toGrade } from "@/components/ws/GradeMark";
import { EmptyState, LoadError, Monogram, PageSkeleton } from "@/ui/primitives";

interface Expired { fact_id: string; signal_type: string; reason: string; expired_at: string; attribute: string; grade: string | null }

function History({ domain }: { domain: string }) {
  const h = useApi<{ history: Expired[] }>(`/v1/accounts/${encodeURIComponent(domain)}/history`);
  const rows = h.data?.history ?? [];
  return (
    <div className="content">
      <div className="account-head">
        <Monogram name={domain} />
        <div>
          <h1>Signals</h1>
          <p className="sub">{domain}</p>
        </div>
      </div>
      <DeepDiveLinks domain={domain} />
      <p className="ink-2 block-gap">Signals that expired or were removed after two absent checks. Nothing is deleted.</p>
      {h.error ? <LoadError reference={null} retryHref={`/accounts/${encodeURIComponent(domain)}/history`} /> : !h.data ? <PageSkeleton /> : rows.length === 0 ? (
        <EmptyState title="Nothing has expired yet" body="When a signal stops being seen, it moves here with the reason and the date." icon={<HistoryIcon size={20} strokeWidth={1.8} />} />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">Signal</th><th scope="col">Grade</th><th scope="col">Reason</th><th scope="col">When</th></tr></thead>
            <tbody>{rows.map((r) => {
              const grade = toGrade(r.grade);
              return (
                <tr key={r.fact_id}>
                  <td>{humanize(r.signal_type)}{r.attribute ? <span className="ink-2">: {cellText(r.attribute, 120)}</span> : null}</td>
                  <td>{grade ? <GradeMark grade={grade} /> : <span className="ink-2">{cellText(r.grade)}</span>}</td>
                  <td>{humanize(r.reason)}</td>
                  <td className="nowrap">{day(r.expired_at)}</td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function HistoryPage() {
  const params = useParams<{ domain: string }>();
  return <SignedIn>{() => <History domain={decodeURIComponent(params.domain)} />}</SignedIn>;
}
