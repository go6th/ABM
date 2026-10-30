// One-screen preview of the structured answer (render_answer, 2026-10-17) for the owner: the
// REAL AnswerView/UsedPanel components server-rendered against a finish()-shaped answer for the
// fictional Synthetic Holdings account, linked to the app's REAL stylesheets. No live model call — deterministic. Run from web/:
//   npx tsx scripts/preview-structured.tsx && npx playwright screenshot --full-page …
import { renderToStaticMarkup } from "react-dom/server";
import { mkdirSync, writeFileSync } from "node:fs";
import { AnswerView, UsedPanel } from "@/components/ask/AnswerView";
import type { ChatAnswer } from "@/lib/gen/api.gen";

const rows: string[][] = [
  ["row 12", "REQ-1042", "Salesforce Solution Architect", "Closed", "2026-03-04", "[1]"],
  ["row 13", "REQ-1043", "Salesforce Integration Lead", "Closed", "2026-03-09", "[1]"],
  ["row 15", "REQ-1048", "CRM Platform Engineer (Salesforce)", "Closed", "2026-03-18", "[1]"],
  ["row 19", "REQ-1051", "Salesforce Data Migration Analyst", "Closed", "2026-03-27", "[1]"],
  ["row 22", "REQ-1057", "Salesforce Marketing Cloud Specialist", "Closed", "5 Apr 2026", "[1]"],
  ["row 24", "REQ-1060", "Salesforce CPQ Consultant", "Closed", "12 Apr 2026", "[1]"],
  ["row 28", "REQ-1066", "Salesforce DevOps Engineer", "Closed", "20 Apr 2026", "[1]"],
  ["row 31", "REQ-1070", "Salesforce Business Analyst", "Closed", "28 Apr 2026", "[1]"],
  ["row 34", "REQ-1074", "Field Sales Representative (DACH)", "Closed", "6 May 2026", "[1]"],
  ["row 36", "REQ-1076", "Field Sales Manager (UK&I)", "Closed", "11 May 2026", "[1]"],
  ["row 38", "REQ-1079", "Salesforce Technical Architect", "Closed", "19 May 2026", "[1]"],
  ["row 41", "REQ-1083", "Field Sales Director (Enterprise)", "Closed", "26 May 2026", "[1]"],
  ["row 44", "REQ-1087", "Salesforce QA Engineer", "Closed", "2 Jun 2026", "[1]"],
  ["row 47", "REQ-1091", "Salesforce Release Manager", "Closed", "9 Jun 2026", "[1]"],
];

const answer = {
  answered: true,
  paragraphs: [],
  structured: {
    headline:
      "**Synthetic Holdings**: no Salesforce-related posting opened since 1 March 2026; relaxing the status (the date stays) finds **14 unique closed postings**, 11 genuine and 3 where salesforce means the field sales team [1].",
    stats: [
      { label: "open since 1 Mar 2026", value: "0" },
      { label: "unique closed matches", value: "14" },
      { label: "false positives", value: "3" },
    ],
    sections: [
      {
        kind: "prose",
        title: "The finding",
        paragraphs: [
          "The strict filter — platform named Salesforce, status Open, first seen on or after 1 March 2026 — returns **0 rows** [1].",
          "Relaxing the status predicate alone (the date and platform stay) returns 14 unique postings after deduplicating on the requisition number [1].",
        ],
      },
      {
        kind: "table",
        title: "Every matching posting (14)",
        columns: ["Sheet row", "Requisition", "Title", "Status", "First seen", "Source"],
        rows,
      },
      {
        kind: "callout",
        tone: "warn",
        text: "Three matches are **false positives**: salesforce there refers to the field sales team, not the platform — rows 34, 36 and 41 [1].",
      },
      {
        kind: "steps",
        title: "Suggested next steps",
        items: [
          "Ask which of the 11 genuine closed roles have successor postings [1].",
          "Watch for a reopened Salesforce requisition at Synthetic Holdings.",
        ],
      },
    ],
  },
  sources: [
    {
      n: 1,
      title: "Holdings_Job_Postings.xlsx · table Jobs",
      meta: "From your files · 644 rows · Internal",
      kind: "table",
      url: null,
      date: null,
    },
  ],
  items: null,
  offer: null,
  followups: ["Which closed roles have successor postings?"],
  used: { workspace: "used", web: "not_used", mail_drive: "not_used", model_name: "kimi-k3" },
  account_domain: "holdings.example",
  account_name: "Synthetic Holdings",
} as unknown as ChatAnswer;

const noop = () => {};
const view = renderToStaticMarkup(
  <div className="ask">
    <div className="ask-scroll">
      <div className="ask-cols">
        <div className="ask-msgs">
          <div className="reply">
            <div className="bubble">
              List all open Salesforce-related jobs first seen on or after March 1, 2026
            </div>
            <AnswerView
              answer={answer}
              openSource={null}
              onOpenSource={noop}
              offerDismissed
              onDismissOffer={noop}
              onFollowUp={noop}
              onPickAccount={noop}
              busy={false}
            />
          </div>
        </div>
        <aside className="ask-side" aria-label="What this answer used">
          <UsedPanel answer={answer} onOpenSource={noop} />
        </aside>
      </div>
    </div>
  </div>,
);

const css = ["tokens", "base", "components", "shell", "ask"]
  .map((n) => `<link rel="stylesheet" href="../ui/${n}.css">`)
  .join("\n  ");
const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Structured answer preview</title>
${css}
</head>
<body>
${view}
</body>
</html>
`;

mkdirSync("shots", { recursive: true });
writeFileSync("shots/structured-preview.html", html);
console.log("wrote shots/structured-preview.html");
