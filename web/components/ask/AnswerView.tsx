"use client";
// One workspace answer as designed: paragraphs with numbered citation chips (the text rendered
// from the Markdown the model wrote: tables, lists, bold, code), an item card when the answer
// has a list, the source card under the answer when a citation is opened, the offer to refresh
// from the web (only on confirmation, never silently) and two follow-up questions. UsedPanel is
// the "What this answer used" card from PDF page 4.
import Link from "next/link";
import type { ChatAnswer } from "@/lib/gen/api.gen";
import { LINK_REL, safeLink } from "@/lib/render";
import { AnswerMarkdown } from "./AnswerMarkdown";
import { AnswerStructured } from "./AnswerStructured";

type Source = ChatAnswer["sources"][number];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function dayMonth(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

export function refreshHref(domain: string): string {
  return `/accounts/${encodeURIComponent(domain)}/refresh`;
}

export function AnswerView({
  answer, openSource, onOpenSource, offerDismissed, onDismissOffer, onFollowUp, onPickAccount, busy,
}: {
  answer: ChatAnswer;
  openSource: number | null;
  onOpenSource: (n: number | null) => void;
  offerDismissed: boolean;
  onDismissOffer: () => void;
  onFollowUp: (q: string) => void;
  onPickAccount: () => void;
  busy: boolean;
}) {
  const source: Source | undefined = answer.sources.find((s) => s.n === openSource);
  const href = source ? safeLink(source.url ?? "") : null;
  const offer = answer.offer;
  // citation n as a chip that opens its source
  const cite = (v: string) => {
    const n = Number(v);
    const s = answer.sources.find((x) => x.n === n);
    return (
      <button
        type="button"
        className="cite"
        aria-expanded={openSource === n}
        aria-label={`Source ${n}${s ? `: ${s.title}` : ""}`}
        onClick={() => onOpenSource(openSource === n ? null : n)}
      >
        {n}
      </button>
    );
  };
  return (
    <div className="reply">
      <div className="answered"><span className="mk" aria-hidden="true" />{answer.answered ? "Answered from your workspace" : "Searched your workspace"}</div>
      {answer.structured ? (
        // the model's render_answer call: cards and tables from the code, identical on every
        // model; paragraphs (the Markdown of the same answer) remain for the stream/store
        <AnswerStructured data={answer.structured} cite={cite} />
      ) : (
        answer.paragraphs.map((para, i) => <AnswerMarkdown key={i} parts={para} cite={cite} />)
      )}
      {answer.items ? (
        <div className="card items-card">
          <div className="lbl">{answer.items.title}</div>
          {answer.items.items.map((it, i) => (
            <div key={i} className="it"><AnswerMarkdown parts={[{ t: "text", v: it }]} cite={cite} inline /></div>
          ))}
        </div>
      ) : null}
      {source ? (
        <div className="card wash src-card" role="region" aria-label={`Source ${source.n}`}>
          <span className="cite static" aria-hidden="true">{source.n}</span>
          <span className="grow"><b>{source.title}</b><br /><span className="mut meta">{source.meta}</span></span>
          <span className="acts">
            {href ? <a className="link" href={href} target="_blank" rel={LINK_REL}>Open source</a> : null}
            <button type="button" className="link" onClick={() => onOpenSource(null)}>Close</button>
          </span>
        </div>
      ) : null}
      {offer && !offerDismissed ? (
        <div className="card wash offer">
          <div className="txt">
            <b>Want anything newer?</b>{" "}
            {offer.refreshed_days_ago !== null && offer.refreshed_days_ago !== undefined
              ? `Your workspace was refreshed ${offer.refreshed_days_ago === 0 ? "today" : offer.refreshed_days_ago === 1 ? "yesterday" : `${offer.refreshed_days_ago} days ago`}.`
              : "Your workspace may not hold this yet."}{" "}
            I can check the web, only if you confirm.
          </div>
          {offer.account_domain ? (
            <Link className="btn primary" href={refreshHref(offer.account_domain)} prefetch={false}>Refresh from web</Link>
          ) : (
            <button type="button" className="btn primary" onClick={onPickAccount}>Refresh from web</button>
          )}
          <button type="button" className="btn" onClick={onDismissOffer}>Stay local</button>
        </div>
      ) : null}
      {answer.followups.length ? (
        <div className="follows">
          {answer.followups.map((f) => (
            <button key={f} type="button" className="chipb sm" disabled={busy} onClick={() => onFollowUp(f)}>{f}</button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function usedLabel(value: string): { text: string; ok: boolean } {
  if (value === "used") return { text: "Used", ok: true };
  if (value === "not_connected") return { text: "Not connected", ok: false };
  return { text: "Not used", ok: false };
}

// The answer while it streams: the checked sentences so far, citation numbers shown but not
// clickable until the finished answer (with its sources) arrives.
export function StreamingAnswer({ paragraphs }: { paragraphs: { t: string; v: string }[][] }) {
  return (
    <div className="reply" aria-busy="true">
      <div className="answered"><span className="pdot" aria-hidden="true" />Writing the answer</div>
      {paragraphs.map((para, i) => (
        <AnswerMarkdown key={i} parts={para} cite={(v) => <span className="cite static" aria-label={`Source ${v}`}>{v}</span>} />
      ))}
    </div>
  );
}

export function UsedPanel({ answer, onOpenSource }: { answer: ChatAnswer; onOpenSource: (n: number) => void }) {
  const rows: Array<[string, string]> = [
    ["Your workspace", answer.used.workspace],
    ["Web", answer.used.web],
    ["Mail and drive", answer.used.mail_drive],
  ];
  return (
    <section className="card used-card" aria-label="What this answer used">
      <div className="lbl">What this answer used</div>
      {rows.map(([label, value]) => {
        const v = usedLabel(value);
        return <div key={label} className="used-row"><span>{label}</span><span className={v.ok ? "ok" : "no"}>{v.text}</span></div>;
      })}
      {answer.used.model_name ? (
        <div className="used-row"><span>Model</span><span className="no">{answer.used.model_name}</span></div>
      ) : null}
      {answer.sources.length ? (
        <>
          <div className="divider" />
          <div className="lbl">Sources</div>
          {answer.sources.map((s) => (
            <div key={s.n} className="used-src">
              <button type="button" className="cite static" aria-label={`Open source ${s.n}`} onClick={() => onOpenSource(s.n)}>{s.n}</button>
              <span>{s.title}{s.date ? ` · ${dayMonth(s.date)}` : ""}</span>
            </div>
          ))}
        </>
      ) : null}
      {answer.account_domain && answer.account_name ? (
        <Link className="link" href={`/accounts/${encodeURIComponent(answer.account_domain)}`} prefetch={false}>Open {answer.account_name} workspace</Link>
      ) : null}
    </section>
  );
}
