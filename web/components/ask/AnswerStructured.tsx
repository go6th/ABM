"use client";
// The model's render_answer call, rendered: a headline card with its headline numbers, then
// the sections in reading order — tables carry EVERY row, prose is sentences, callouts flag
// one finding, steps say what to do next. Model output is untrusted: strings render through a
// cite/bold tokenizer, never as HTML. All styling is classes (the CSP forbids inline styles).
// A column whose value is the same on every row folds into a badge by the title ("All
// closed") and leaves the table; rows identical in every cell merge into one with a ×N badge.
import { Fragment, type ReactNode } from "react";
import type { AnswerSection, AnswerStructured as Structured } from "@/lib/gen/api.gen";

// [n] citations and **bold** inside the answer's strings; no other markup is recognised
const TOKEN = /(\[\d{1,3}\])|(\*\*.+?\*\*)/g;
// a date renders on one line (never wrapped mid-date at any width)
const DATE = /^(\d{4}-\d{2}-\d{2}|\d{1,2} [A-Z][a-z]{2} \d{4}|[A-Z][a-z]{2} \d{4})$/;

export function citedText(text: string, cite: (n: string) => ReactNode): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<Fragment key={`c${out.length}`}>{cite(m[1].slice(1, -1))}</Fragment>);
    else if (m[2]) out.push(<strong key={`b${out.length}`}>{m[2].slice(2, -2)}</strong>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function Cell({ value, cite }: { value: string; cite: (n: string) => ReactNode }) {
  return (
    <span className={DATE.test(value.trim()) ? "sa-nowrap" : undefined}>{citedText(value, cite)}</span>
  );
}

function AnswerTable({ section, cite }: { section: AnswerSection; cite: (n: string) => ReactNode }) {
  const columns = section.columns ?? [];
  const rows = (section.rows ?? []).filter((r) => r.length === columns.length);
  // fold a column whose value is the same on every row into a badge by the title
  const uniform = new Set<number>();
  if (rows.length >= 2) {
    columns.forEach((_c, ci) => {
      if (rows.every((r) => r[ci] === rows[0]![ci])) uniform.add(ci);
    });
  }
  const keep = columns.map((_c, ci) => ci).filter((ci) => !uniform.has(ci));
  // merge rows identical in every cell into one with a ×N badge
  const merged: Array<{ cells: string[]; n: number }> = [];
  const seen = new Map<string, number>();
  for (const row of rows) {
    const key = row.join("");
    const at = seen.get(key);
    if (at === undefined) {
      seen.set(key, merged.length);
      merged.push({ cells: row, n: 1 });
    } else {
      merged[at]!.n += 1;
    }
  }
  return (
    <section className="sa-section">
      {section.title ? <div className="sa-title">{section.title}</div> : null}
      {uniform.size ? (
        <div className="sa-badges">
          {[...uniform].map((ci) => (
            <span key={ci} className="sa-badge">
              {columns[ci]}: {citedText(rows[0]![ci] ?? "", cite)}
            </span>
          ))}
        </div>
      ) : null}
      <div className="sa-frame md-table">
        <table>
          <thead>
            <tr>
              {keep.map((ci) => (
                <th key={ci}>{columns[ci]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {merged.map((row, ri) => (
              <tr key={ri}>
                {keep.map((ci) => (
                  <td key={ci}>
                    <Cell value={row.cells[ci] ?? ""} cite={cite} />
                    {row.n > 1 && ci === keep[0] ? (
                      <span className="sa-dup" aria-label={`${row.n} identical rows`}>
                        ×{row.n}
                      </span>
                    ) : null}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {section.note ? (
        <div className="sa-note">{citedText(section.note, cite)}</div>
      ) : null}
    </section>
  );
}

export function AnswerStructured({
  data,
  cite,
}: {
  data: Structured;
  cite: (n: string) => ReactNode;
}) {
  return (
    <>
      <div className="card sa-head">
        <p className="sa-headline">{citedText(data.headline, cite)}</p>
        {data.stats.length ? (
          <div className="sa-stats">
            {data.stats.map((s, i) => (
              <span key={i} className="sa-stat">
                <b>{citedText(s.value, cite)}</b> {s.label}
              </span>
            ))}
          </div>
        ) : null}
      </div>
      {data.sections.map((section, i) => {
        if (section.kind === "table") return <AnswerTable key={i} section={section} cite={cite} />;
        if (section.kind === "callout") {
          const tone = section.tone === "warn" || section.tone === "good" ? section.tone : "info";
          return (
            <div key={i} className={`card sa-callout ${tone}`} role="note">
              {section.title ? <div className="sa-title">{section.title}</div> : null}
              <div className="sa-callout-text">{citedText(section.text ?? "", cite)}</div>
            </div>
          );
        }
        if (section.kind === "steps") {
          return (
            <section key={i} className="sa-section">
              {section.title ? <div className="sa-title">{section.title}</div> : null}
              <ol className="sa-steps">
                {(section.items ?? []).map((item, j) => (
                  <li key={j}>{citedText(item, cite)}</li>
                ))}
              </ol>
            </section>
          );
        }
        return (
          <section key={i} className="sa-section">
            {section.title ? <div className="sa-title">{section.title}</div> : null}
            {(section.paragraphs ?? []).map((para, j) => (
              <p key={j} className="sa-prose">
                {citedText(para, cite)}
              </p>
            ))}
          </section>
        );
      })}
    </>
  );
}
