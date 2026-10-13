"use client";
// The Internal Only view of a "From your files" run: what the uploaded documents say, each finding
// with its grade and its citation (document and place) and the quoted passage; the documents
// read; the Internal Only Word file; and, for an enriched run, the public findings in their own
// list labelled Public research. Never shown to viewers; every open is an audit event.
import { useApi } from "@/lib/useApi";
import { humanize } from "@/lib/ws-format";
import { Badge, Banner, Section } from "@/ui/primitives";
import type { InternalView as InternalViewData } from "@/lib/gen/api.gen";

const CATEGORY: Record<string, string> = {
  platforms: "Platforms and systems",
  incumbent_partners: "Incumbent partners and vendors",
  programs: "Programmes and initiatives",
  budget_cycles: "Budget cycles and dates",
  objections: "Objections and risks",
  decisions: "Decisions and stages",
  next_steps: "Next steps",
  people: "People and roles",
};

export function InternalView({ runId }: { runId: string }) {
  const view = useApi<InternalViewData>(`/v1/runs/${encodeURIComponent(runId)}/internal`);
  if (view.error) return <Banner kind="error">The internal view could not be loaded. Reload the page to try again.</Banner>;
  if (!view.data) return <p className="ink-2">Loading…</p>;
  const data = view.data;
  const groups = Object.keys(CATEGORY)
    .map((cat) => ({ cat, items: data.findings.filter((f) => f.category === cat) }))
    .filter((g) => g.items.length);
  return (
    <>
      <Section
        title="Internal Only"
        sub={data.label}
        action={data.file_available ? <a className="btn quiet" href={`/v1/runs/${encodeURIComponent(runId)}/internal/file`}>Download Internal Only file</a> : undefined}
      >
        <div className="stack">
          <Banner kind="info">This view is for your team only. It is never part of a client file, a shared folder or a package.</Banner>
          <div className="card stack">
            <p className="strong">Documents read</p>
            <ul className="stack tight">
              {data.documents.map((d) => (
                <li key={String(d.document_id)} className="row between">
                  <span>{String(d.name)}</span>
                  <span className="ink-2 t-footnote">{String(d.passages)} passages</span>
                </li>
              ))}
            </ul>
          </div>
          {groups.length ? groups.map((g) => (
            <div key={g.cat} className="card stack">
              <p className="strong">{CATEGORY[g.cat]}</p>
              <ul className="stack">
                {g.items.map((f, i) => (
                  <li key={`${g.cat}-${i}`}>
                    <div className="row between">
                      <span>{f.value}</span>
                      <span className="row nowrap">
                        <Badge tone="butter">From your files</Badge>
                        <Badge tone={f.grade === "confirmed" ? "mint" : "peri"}>{f.grade === "confirmed" ? "Confirmed" : "Internal"}</Badge>
                      </span>
                    </div>
                    <p className="ink-2 t-footnote">Source: {f.citation}</p>
                    <blockquote className="quote">{f.quote}</blockquote>
                  </li>
                ))}
              </ul>
            </div>
          )) : <p className="ink-2">The uploaded documents state nothing in the researched areas.</p>}
        </div>
      </Section>
      {data.public_run_id ? (
        <Section title="Public research" sub="Findings of the linked public research run, kept apart from your files.">
          {data.public_findings.length ? (
            <ul className="rows">
              {data.public_findings.map((f, i) => (
                <li key={i} className="list-row">
                  <div><p>{String(f.value ?? "")}</p><p className="context">{humanize(String(f.attribute ?? "").split(".").slice(-1)[0] ?? "")}</p></div>
                  <div className="meta"><Badge tone="peri">Public research</Badge><Badge>{humanize(String(f.grade ?? ""))}</Badge></div>
                </li>
              ))}
            </ul>
          ) : <p className="ink-2">The public research has no findings yet. <a className="link" href={`/runs/${encodeURIComponent(data.public_run_id)}`}>Open the public run</a>.</p>}
        </Section>
      ) : null}
    </>
  );
}
