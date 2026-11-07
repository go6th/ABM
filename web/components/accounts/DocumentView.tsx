// A whole uploaded document as the product read it (workspace import phase 1, 2026-11-03): the
// file's name and status, then its passages in order. A zip shows one section per file inside
// it, in the member list's order, so a file that was skipped or failed is listed with its
// reason even though it has no passages. Pure props: the page fetches, this only draws.
import type { DocumentMember, DocumentPassage, DocumentStatus } from "@/lib/gen/api.gen";
import { ROLE_TEXT, STAGE_TEXT, memberLine, stageOf } from "@/lib/documents";
import { cellText } from "@/lib/render";

export interface DocumentSection { member: DocumentMember | null; path: string | null; passages: DocumentPassage[] }

// passage text keeps its line breaks (a table row per line); only control characters go
const CONTROL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;

/** The member path at the start of a zip passage's citation ("plans/renewal.docx, page 3"). A
 * citation that is only the member path ("plans/renewal.docx") matches too; one that names no
 * known member counts as the document itself. */
export function memberOf(where: string | null, members: string[]): string | null {
  if (!where) return null;
  for (const m of members) {
    if (where === m || where.startsWith(`${m}, `)) return m;
  }
  return null;
}

/** The citation without its member path, so a section does not repeat its own file's name. */
function placeOf(where: string | null, member: string | null): string | null {
  if (!where) return null;
  if (member && where === member) return null;
  if (member && where.startsWith(`${member}, `)) return where.slice(member.length + 2);
  return where;
}

/** Sections in the member list's order (members with no passages included), then any passages
 * whose citation names no member, then a single section for a plain file. */
export function sectionsOf(members: DocumentMember[], passages: DocumentPassage[]): DocumentSection[] {
  const sorted = [...passages].sort((a, b) => a.seq - b.seq);
  if (!members.length) return [{ member: null, path: null, passages: sorted }];
  // longest paths first, so "a/b.docx" never claims a passage of "a/b.docx.bak"
  const paths = members.map((m) => m.member).sort((a, b) => b.length - a.length);
  const by = new Map<string | null, DocumentPassage[]>();
  for (const p of sorted) {
    const key = memberOf(p.where, paths);
    const list = by.get(key);
    if (list) list.push(p);
    else by.set(key, [p]);
  }
  const out: DocumentSection[] = members.map((m) => ({ member: m, path: m.member, passages: by.get(m.member) ?? [] }));
  const rest = by.get(null);
  if (rest?.length) out.push({ member: null, path: null, passages: rest });
  return out;
}

export function paragraphs(content: string): string[] {
  return content.replace(CONTROL, "").split(/\n\s*\n/).map((s) => s.trim()).filter(Boolean);
}

function Passages({ passages, member }: { passages: DocumentPassage[]; member: string | null }) {
  return (
    <ol className="doc-passages" aria-label="Passages">
      {passages.map((p, i) => {
        const heading = p.heading ?? null;
        // a heading line when the heading changes from the passage before
        const changed = i === 0 || heading !== (passages[i - 1]!.heading ?? null);
        const place = placeOf(p.where, member);
        const table = p.kind === "table" || p.kind === "rows";
        return (
          <li key={p.seq} className="doc-passage">
            {changed && heading ? <h3 className="doc-heading">{cellText(heading)}</h3> : null}
            {place ? <span className="sub">{cellText(place)}</span> : null}
            {table ? (
              <pre className="mono">{p.content.replace(CONTROL, "")}</pre>
            ) : (
              paragraphs(p.content).map((text, i) => <p key={i} className="doc-text">{text}</p>)
            )}
          </li>
        );
      })}
    </ol>
  );
}

export function DocumentView({ doc, members, passages }: { doc: DocumentStatus; members: DocumentMember[]; passages: DocumentPassage[] }) {
  const sections = sectionsOf(members, passages);
  const zip = members.length > 0;
  const status = STAGE_TEXT[stageOf(doc)];
  const sub = [
    status,
    doc.audience === "owner_only" ? "Owner only" : null,
    doc.passages ? `${doc.passages.toLocaleString()} sections` : null,
    zip ? `${members.length.toLocaleString()} file${members.length === 1 ? "" : "s"} inside` : null,
  ].filter(Boolean).join(" · ");
  return (
    <article className="doc-view stack-14" aria-labelledby="doc-title">
      <div className="ttl">
        <h2 id="doc-title">{cellText(doc.name)}</h2>
        <div className="sub">{sub}</div>
      </div>
      {passages.length === 0 && !zip ? (
        <p className="mut small">Nothing was read from this file{doc.message ? `: ${doc.message}` : "."}</p>
      ) : null}
      {sections.map((s, i) => (
        <section key={s.path ?? `rest-${i}`} className="card pad stack-10 doc-section" id={s.path ? `m-${i}` : undefined} aria-label={s.path ?? (zip ? "Other sections" : cellText(doc.name))}>
          {zip ? (
            <div className="doc-member">
              <h2>{s.path ? cellText(s.path) : "Other sections"}</h2>
              {s.member ? (
                <span className="sub">
                  {ROLE_TEXT[s.member.role] ?? "File"}{s.member.audience === "owner_only" ? " · owner only" : ""} · {memberLine(s.member)}
                </span>
              ) : null}
            </div>
          ) : null}
          {s.passages.length ? (
            <Passages passages={s.passages} member={s.path} />
          ) : s.member ? (
            <p className="mut small">{s.member.status === "read" ? "Nothing was read from this file." : "This file was not read."}</p>
          ) : null}
        </section>
      ))}
    </article>
  );
}
