// Chat stream helpers and the answer renderer. Model output is untrusted: markdown without raw
// HTML, only http(s) links, opened with noopener noreferrer nofollow. Fact citations such as
// [F3] become numbered inline links that open the fact in the account's Sources table.
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError } from "@/lib/api";
import { LINK_REL, safeLink } from "@/lib/render";

export interface Frame { event: string; data: Record<string, unknown> }

export function parseFrame(raw: string): Frame | null {
  let event = "message";
  const data: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try {
    const value: unknown = JSON.parse(data.join("\n"));
    return typeof value === "object" && value !== null ? { event, data: value as Record<string, unknown> } : null;
  } catch {
    return null;
  }
}

export async function readFrames(reply: Response, onFrame: (frame: Frame) => void): Promise<void> {
  const reader = reply.body?.getReader();
  if (!reader) throw new ApiError(502, "stream_unavailable", "");
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = parseFrame(buffer.slice(0, boundary));
      if (frame) onFrame(frame);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
}

const CITE = /\[([A-Z]{1,3}\d{1,5})\]/g;

/** The distinct fact references of an answer, in order of first use. */
export function citations(text: string): string[] {
  const seen: string[] = [];
  for (const match of text.matchAll(CITE)) {
    const ref = match[1]!;
    if (!seen.includes(ref)) seen.push(ref);
  }
  return seen;
}

/** [F3] becomes a markdown link to the fact, numbered by first use. */
export function numberCitations(text: string, refs: string[], sourcesHref: (ref: string) => string): string {
  return text.replace(CITE, (_m, ref: string) => {
    const n = refs.indexOf(ref) + 1;
    return n > 0 ? `[${n}](${sourcesHref(ref)} "Source ${ref}")` : `[${ref}]`;
  });
}

/** Phrases the grounded assistant uses when the workspace holds nothing for the question. */
export function cannotAnswer(text: string): boolean {
  const t = text.toLowerCase();
  return /not held|no completed research|not researched|not in (the|this|your) workspace|does not hold|refresh from web/.test(t);
}

function components(sourcesHref: ((ref: string) => string) | null): Components {
  return {
    a: ({ href, children, title }) => {
      const link = href ?? "";
      if (sourcesHref && link.startsWith("/") && typeof title === "string" && title.startsWith("Source ")) {
        return <a className="cite" href={link} title={title}>{children}</a>;
      }
      const safe = safeLink(link);
      return safe ? <a href={safe} target="_blank" rel={`${LINK_REL} nofollow`}>{children}</a> : <span>{children}</span>;
    },
    img: () => null,
  };
}

export function Answer({ text, streaming, sourcesHref }: { text: string; streaming: boolean; sourcesHref?: (ref: string) => string }) {
  const refs = sourcesHref ? citations(text) : [];
  const body = sourcesHref ? numberCitations(text, refs, sourcesHref) : text;
  return (
    <div className={streaming ? "a streaming" : "a"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={components(sourcesHref ?? null)}>{body}</ReactMarkdown>
      {streaming && !text ? <p /> : null}
      {!streaming && refs.length && sourcesHref ? (
        <ol className="sources gap-top" aria-label="Sources">
          {refs.map((ref, i) => <li key={ref}><a className="cite" href={sourcesHref(ref)}>{i + 1}</a> Fact {ref}</li>)}
        </ol>
      ) : null}
    </div>
  );
}
