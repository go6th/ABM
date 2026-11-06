// The banner on the account page when a part of the latest file import failed or has not
// finished (ingestion hardening PR2): which file, which part, what state, and a link to the
// Uploaded files sheet, which shows the status per file. It only reads the workspace reply.
import Link from "next/link";
import type { ImportProblem } from "@/lib/gen/api.gen";

const PART_LABEL: Record<string, string> = {
  jobs: "Job postings",
  people: "People list",
  tech: "Tech stack and teams",
  docx: "Word summary and evidence",
  raw: "Raw pulls and notes",
};

/** One problem in words: "Job postings failed (Acme.zip)" or "People list still pending (...)". */
export function describeProblem(p: ImportProblem): string {
  const part = PART_LABEL[p.part] ?? p.part;
  const state = p.status === "failed" ? "failed" : p.status === "running" ? "still running" : "still pending";
  return `${part} ${state} (${p.document_name})`;
}

export function ImportProblems({ problems, sheetHref }: { problems: ImportProblem[]; sheetHref: string }) {
  if (!problems.length) return null;
  const failed = problems.some((p) => p.status === "failed");
  return (
    <div className={failed ? "notice error" : "notice warn"} role="status" data-testid="import-problems">
      <p>
        <b>Some parts of the latest file import did not finish: </b>
        {problems.map(describeProblem).join("; ")}.{" "}
        <Link href={sheetHref} prefetch={false}>See each file in Uploaded files</Link>
      </p>
    </div>
  );
}
