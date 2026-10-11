"use client";
// Run inspection through a tenant-approved grant, and the pre-pulled data upload for a run. The
// browser never uploads the file itself (the CSP allows connections to this origin only): the
// page opens an upload intent, shows the exact file spec and the signed upload address for a
// staff tool, then records completion. File content never passes through this page.
import { useState, type FormEvent } from "react";
import { Copy, Upload } from "lucide-react";
import { ApiError } from "@/lib/api";
import { operatorRequest } from "@/lib/operator-api";
import { formatDate, formatDuration } from "@/lib/operator-render";
import { humanize } from "@/lib/ws-format";
import { OperatorRequestState } from "../OperatorStates";
import { SupportGrantLookup, type SupportLookup } from "../SupportGrantLookup";
import { useOperatorApi } from "../useOperatorApi";
import { useOperatorViewer } from "../OperatorSessionGate";
import { Badge, Banner, EmptyState, PageHead, Section, Tile } from "@/ui/primitives";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function RunTimelineView({ lookup }: { lookup: SupportLookup }) {
  const result = useOperatorApi("GET /operator/v1/runs/{run_id}/timeline", {
    params: { run_id: lookup.runId ?? "" }, headers: { "X-Support-Grant": lookup.grantId },
  });
  const data = result.data;
  return (
    <div className="block-gap">
      <OperatorRequestState loading={result.loading} error={result.error} onRetry={result.reload} />
      {data ? <>
        <div className="grid-3">
          <Tile tone="peri" label="Run status" value={humanize(data.status)} note={`Created ${formatDate(data.created_at)}`} />
          <Tile tone="lilac" label="Makespan" value={formatDuration(data.makespan_s)} note={`Critical path ${formatDuration(data.critical_path_s)}`} />
          <Tile tone="butter" label="Idle time" value={formatDuration(data.idle_s)} note={`Trace ${data.trace_id}`} />
        </div>
        <Section title="Run units" id="units" sub={`Timings derived from events. ${data.units.length} rows.`}>
          {data.missing_sources.length ? <Banner kind="warn">Some event sources are unavailable: {data.missing_sources.join(", ")}</Banner> : null}
          {data.units.length ? (
            <div className="table-wrap gap-top"><table className="table"><caption className="sr-only">Run unit queue, execution and lease timings</caption>
              <thead><tr><th scope="col">Unit</th><th scope="col">Outcome</th><th scope="col" className="num">Queue wait</th><th scope="col" className="num">Run time</th><th scope="col" className="num">Lease wait</th></tr></thead>
              <tbody>{data.units.map((unit, index) => <tr key={unit.unit_key + ":" + index}><td><code className="mono t-footnote">{unit.unit_key}</code></td><td>{unit.outcome ?? <span className="ink-2">Not recorded</span>}</td><td className="num">{formatDuration(unit.queue_wait_s)}</td><td className="num">{formatDuration(unit.run_s)}</td><td className="num">{formatDuration(unit.lease_wait_s)}</td></tr>)}</tbody>
            </table></div>
          ) : <EmptyState title="No unit timings returned" body="The API returned the timeline with no unit rows." />}
        </Section>
      </> : null}
    </div>
  );
}

function PrePulledUpload() {
  const [runId, setRunId] = useState("");
  const [intent, setIntent] = useState<{ intent_id: string; spec: Record<string, unknown>; upload: { url: string; content_type: string; max_bytes: number; expires_at: string } } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pullDate, setPullDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const open = async (event: FormEvent) => {
    event.preventDefault();
    if (!UUID.test(runId.trim())) { setMessage({ ok: false, text: "Enter a valid run UUID." }); return; }
    setBusy(true); setMessage(null);
    try {
      const r = await operatorRequest("POST /operator/v1/runs/{run_id}/prepulled/intents", { params: { run_id: runId.trim() }, body: { intent_id: crypto.randomUUID() } });
      setIntent(r);
    } catch (error) {
      setMessage({ ok: false, text: error instanceof ApiError ? `${error.detail} (${error.type})` : "The upload could not be opened." });
    } finally { setBusy(false); }
  };
  const complete = async () => {
    if (!intent) return;
    setBusy(true); setMessage(null);
    try {
      const v = await operatorRequest("POST /operator/v1/runs/{run_id}/prepulled/{intent_id}/complete", {
        params: { run_id: runId.trim(), intent_id: intent.intent_id }, body: { account_confirmed: confirmed, pull_date: pullDate || null },
      });
      setMessage(v.accepted
        ? { ok: true, text: `Accepted ${v.record_count ?? 0} records for ${v.operations.length} operations.` }
        : { ok: false, text: `Not accepted${v.reason_code ? `: ${humanize(v.reason_code)}` : ""}.` });
    } catch (error) {
      setMessage({ ok: false, text: error instanceof ApiError ? `${error.detail} (${error.type})` : "Completion could not be recorded." });
    } finally { setBusy(false); }
  };
  return (
    <div className="card stack">
      <form className="form-row" onSubmit={(e) => void open(e)} aria-label="Open a pre-pulled upload">
        <div className="field"><label htmlFor="pp-run">Run for the upload</label><input id="pp-run" className="input mono" value={runId} onChange={(e) => { setRunId(e.target.value); setIntent(null); }} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" /></div>
        <button className="btn" type="submit" disabled={busy}><Upload size={16} aria-hidden="true" />Open upload</button>
      </form>
      {intent ? (
        <>
          <dl className="dl">
            <dt>Intent</dt><dd className="mono t-footnote">{intent.intent_id}</dd>
            <dt>Content type</dt><dd className="mono t-footnote">{intent.upload.content_type}</dd>
            <dt>Largest file</dt><dd>{Math.round(intent.upload.max_bytes / 1024 / 1024)} MB</dd>
            <dt>Expires</dt><dd>{formatDate(intent.upload.expires_at)}</dd>
          </dl>
          <div className="field">
            <span className="label">Signed upload address</span>
            <div className="row nowrap">
              <code className="mono t-footnote truncate grow">{intent.upload.url}</code>
              <button type="button" className="btn sm" onClick={() => void navigator.clipboard.writeText(intent.upload.url).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1600); })}><Copy size={14} aria-hidden="true" />{copied ? "Copied" : "Copy"}</button>
            </div>
            <p className="hint">Upload the file to this address with a PUT from your staff tool, then record completion here.</p>
          </div>
          <details className="disclosure"><summary>File spec</summary><pre className="code">{JSON.stringify(intent.spec, null, 2)}</pre></details>
          <label className="check"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />The file is for this run&apos;s account</label>
          <div className="field"><label htmlFor="pp-date">Pull date</label><input id="pp-date" className="input" type="date" value={pullDate} onChange={(e) => setPullDate(e.target.value)} /></div>
          <div><button className="btn primary" type="button" disabled={busy || !confirmed} onClick={() => void complete()}>Record completion</button></div>
        </>
      ) : null}
      {message ? <p className={message.ok ? "inline-status" : "inline-error"} role="status">{message.text}</p> : null}
    </div>
  );
}

export default function OperatorRunsPage() {
  const viewer = useOperatorViewer();
  return (
    <div>
      <PageHead title="Run inspection" sub="Inspect one run through an active tenant-approved support grant." />
      <Banner kind="info" title="No global run directory is exposed" role="note">The API returns a timeline only for a run ID and grant you provide. Reads are audited against that tenant.</Banner>
      <div className="block-gap"><SupportGrantLookup runRequired>{(lookup) => lookup ? <RunTimelineView lookup={lookup} /> : null}</SupportGrantLookup></div>
      {viewer.role === "operator" ? (
        <Section title="Pre-pulled data" id="prepulled" sub="Attach a pulled data file to a run. Only counts and events are shown here." action={<Badge tone="lilac">Operator only</Badge>}>
          <PrePulledUpload />
        </Section>
      ) : null}
    </div>
  );
}
