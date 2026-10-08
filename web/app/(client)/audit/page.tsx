"use client";
// The tenant's audit trail (admins): reads, runs, exports, administration and support access,
// identifiers only. The CSV export shows categories, never providers, credits or cost.
import { useEffect, useState } from "react";
import { api, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { cellText } from "@/lib/render";

interface Ev { seq: number; at: string; actor: string; actor_kind: string; action: string; resource_type: string; resource_id: string | null; outcome: string }

function Audit({ s }: { s: Session }) {
  const [events, setEvents] = useState<Ev[]>([]);
  useEffect(() => { if (s.role === "admin") void api<{ events: Ev[] }>("GET", "/v1/audit?limit=100").then((b) => setEvents(b.events)); }, [s.role]);
  if (s.role !== "admin") return <p>Only admins read the audit trail.</p>;
  return (
    <section>
      <h1>Audit</h1>
      <p><a href="/v1/audit/export">Download the audit trail (CSV)</a></p>
      <table className="data"><thead><tr><th scope="col">#</th><th scope="col">When</th><th scope="col">Who</th><th scope="col">What</th><th scope="col">Resource</th><th scope="col">Outcome</th></tr></thead>
        <tbody>{events.map((e) => (
          <tr key={e.seq}><td>{e.seq}</td><td>{cellText(e.at)}</td><td>{cellText(e.actor)}{e.actor_kind === "support" ? " (6th support)" : ""}</td>
            <td>{cellText(e.action)}</td><td>{cellText(e.resource_type)} {cellText(e.resource_id)}</td><td>{e.outcome}</td></tr>
        ))}</tbody></table>
    </section>
  );
}

export default function AuditPage() { return <SignedIn>{(s) => <Audit s={s} />}</SignedIn>; }
