// Overview: one paragraph summary, Key findings (graded) after a first scan, and What changed
// since <date> only after a later refresh. An account built from files shows the file analysis.
import type { AccountDetail, History, TablePage } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { GradeMark, toGrade } from "@/components/ws/GradeMark";
import { FileAnalysis } from "@/components/ws/FileAnalysis";
import { LoadError } from "@/ui/primitives";
import { m } from "@/lib/messages";
import { day, humanize, runState } from "@/lib/ws-format";
import { cellText } from "@/lib/render";

export const dynamic = "force-dynamic";

const ORDER = ["confirmed", "probable", "hypothesis", "lead"];

export default async function AccountOverview({ params }: { params: Promise<{ domain: string }> }) {
  const domain = decodeURIComponent((await params).domain);
  const base = `/v1/accounts/${encodeURIComponent(domain)}`;
  const here = `/accounts/${encodeURIComponent(domain)}`;
  let account: AccountDetail | null = null;
  let facts: TablePage | null = null;
  let history: History | null = null;
  let reference: string | null | undefined;
  try {
    account = await serverGet<AccountDetail>(base, here);
    if (account) [facts, history] = await Promise.all([serverGet<TablePage>(`${base}/tables/facts`, here, [409]), serverGet<History>(`${base}/history`, here)]);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    reference = e.reference;
  }
  if (reference !== undefined) return <LoadError reference={reference} retryHref={here} />;
  if (!account) return null;
  const done = [...account.runs].filter((r) => runState(r.status) === "done").sort((a, b) => b.created_at.localeCompare(a.created_at));
  const latest = done[0] ?? null;
  const previous = done[1] ?? null;
  const fileRun = latest?.mode === "file" ? latest : null;
  const known = (facts?.rows ?? [])
    .map((r) => ({ id: r.row_id ?? String(r.position), ref: r.ref, grade: toGrade(r.cells.grade), finding: cellText(r.cells.finding ?? r.cells.value, 400), question: cellText(r.cells.question, 300), url: r.source.url }))
    .filter((f) => f.finding && f.grade)
    .sort((a, b) => ORDER.indexOf(a.grade!) - ORDER.indexOf(b.grade!));
  const summary = known.filter((f) => f.grade === "confirmed").slice(0, 3).map((f) => f.finding.replace(/\.?$/, "."));
  const changed = (history?.history ?? []).slice(0, 8);
  return (
    <div className="stack-lg">
      <p className="summary">{summary.length ? summary.join(" ") : m.account.summaryEmpty}</p>
      {fileRun ? <FileAnalysis runId={fileRun.run_id} domain={domain} /> : null}
      <section className="section" aria-labelledby="findings">
        <h2 id="findings">{m.account.findings}</h2>
        {known.length ? (
          <ul className="facts">
            {known.slice(0, 12).map((f) => (
              <li key={f.id}>
                <GradeMark grade={f.grade!} />
                <div>
                  <p>{f.finding}</p>
                  {f.grade === "hypothesis" && f.question ? <p className="cite">{f.question}</p> : null}
                  {f.ref ? <p className="cite"><a href={`${here}/sources?ref=${encodeURIComponent(f.ref)}`}>Source {cellText(f.ref)}</a></p> : null}
                </div>
              </li>
            ))}
          </ul>
        ) : <p className="muted">{m.account.findingsEmpty}</p>}
      </section>
      {previous ? (
        <section className="section" aria-labelledby="changed">
          <h2 id="changed">{m.account.changed(day(previous.finished_at ?? previous.created_at))}</h2>
          {changed.length ? (
            <ul className="rows">
              {changed.map((c) => {
                const grade = toGrade(c.grade);
                return (
                  <li key={c.fact_id}>
                    <div>
                      <p>{humanize(c.signal_type)}{c.attribute ? `: ${cellText(c.attribute, 120)}` : ""}</p>
                      <span className="sub">{cellText(c.reason, 200)}</span>
                    </div>
                    <div className="end">{grade ? <GradeMark grade={grade} /> : null} {day(c.expired_at)}</div>
                  </li>
                );
              })}
            </ul>
          ) : <p className="muted">{m.account.changedEmpty}</p>}
        </section>
      ) : null}
    </div>
  );
}
