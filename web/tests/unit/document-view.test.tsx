// @vitest-environment jsdom
// The document page's view: a zip groups its passages under each file inside it in the member
// list's order (files with nothing read are listed with their reason), a heading line appears
// when the heading changes, table rows keep their text in a mono block, an owner-only file says
// so in its sub line, and a plain file with nothing read says that.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { DocumentView, memberOf, paragraphs, sectionsOf } from "@/components/accounts/DocumentView";
import type { DocumentMember, DocumentPassage, DocumentStatus } from "@/lib/gen/api.gen";

afterEach(cleanup);

const doc = (over: Partial<DocumentStatus>): DocumentStatus => ({
  document_id: "d1", account_id: "a1", name: "BD_workspace.zip", status: "indexed", stage: "indexed", declared_type: "zip",
  detected_type: "zip", size_bytes: 59392, reason: null, message: null, passages: 3, redactions: 0, redaction_tier: "standard",
  authoritative: false, company: null, needs_company: false, created_at: "2026-11-03T00:00:00Z", parts_total: 1, parts_done: 1,
  parts_failed: [], indexed_passages: 3, members_read: 2, members_skipped: [], audience: "client",
  ...over,
});

const member = (over: Partial<DocumentMember>): DocumentMember => ({
  id: "m1", member: "ws/notes/plan.md", role: "note", declared_type: "md", bytes: 100, status: "read", reason: null, passages: 1, tables: 0, audience: "client",
  ...over,
});

const MEMBERS: DocumentMember[] = [
  member({ member: "ws/deliverables/BD_Job_Postings.xlsx", role: "deliverable_jobs", passages: 1, tables: 1 }),
  member({ member: "ws/notes/plan.md", role: "note", passages: 2 }),
  member({ member: "ws/BD_Owner_Only.docx", role: "owner_only", status: "skipped", reason: "an owner-only file; upload it on its own", passages: 0, audience: "owner_only" }),
];

const PASSAGES: DocumentPassage[] = [
  { seq: 2, kind: "section", where: "ws/notes/plan.md, section Budget", heading: "Budget", content: "Dana owns the budget.\n\nRenewal in March." },
  { seq: 0, kind: "table", where: "ws/deliverables/BD_Job_Postings.xlsx, row 2 of Jobs", heading: "Jobs", content: "Title | Location\nData engineer | Boston" },
  { seq: 1, kind: "section", where: "ws/notes/plan.md, section Plan", heading: "Plan", content: "Start with the people list." },
];

test("passages are grouped under their file inside the zip, in the member list's order", () => {
  expect(memberOf("ws/notes/plan.md, section Plan", ["ws/notes/plan.md"])).toBe("ws/notes/plan.md");
  expect(memberOf("ws/notes/plan.md", ["ws/notes/plan.md"])).toBe("ws/notes/plan.md");
  expect(memberOf("page 3", ["ws/notes/plan.md"])).toBeNull();
  const sections = sectionsOf(MEMBERS, PASSAGES);
  expect(sections.map((s) => s.path)).toEqual(["ws/deliverables/BD_Job_Postings.xlsx", "ws/notes/plan.md", "ws/BD_Owner_Only.docx"]);
  expect(sections[1]!.passages.map((p) => p.seq)).toEqual([1, 2]); // seq order inside a section
  expect(sections[2]!.passages).toEqual([]); // a skipped member is listed with nothing read
  // a plain file: one section with every passage in seq order
  expect(sectionsOf([], PASSAGES).map((s) => s.passages.map((p) => p.seq))).toEqual([[0, 1, 2]]);
  expect(paragraphs("One.\n\n\nTwo.\n")).toEqual(["One.", "Two."]);
});

test("a zip renders one section per file with headings, paragraphs, table text and reasons", () => {
  render(<DocumentView doc={doc({})} members={MEMBERS} passages={PASSAGES} />);
  expect(screen.getByRole("heading", { level: 1, name: "BD_workspace.zip" })).toBeTruthy();
  expect(screen.getByText("Ready · 3 passages · 3 files inside")).toBeTruthy();
  const notes = within(screen.getByRole("region", { name: "ws/notes/plan.md" }));
  expect(notes.getByText("Note · Read: 2 passages")).toBeTruthy();
  // the heading line appears when the heading changes, and the citation drops the member path
  expect(notes.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["Plan", "Budget"]);
  expect(notes.getByText("section Budget")).toBeTruthy();
  expect(notes.getByText("Dana owns the budget.")).toBeTruthy();
  expect(notes.getByText("Renewal in March.")).toBeTruthy();
  const jobs = within(screen.getByRole("region", { name: "ws/deliverables/BD_Job_Postings.xlsx" }));
  expect(jobs.getByText("Job postings · Read: 1 passages, 1 table")).toBeTruthy();
  const pre = jobs.getByText(/Data engineer/);
  expect(pre.tagName).toBe("PRE");
  expect(pre.className).toBe("mono");
  expect(pre.textContent).toBe("Title | Location\nData engineer | Boston");
  const skipped = within(screen.getByRole("region", { name: "ws/BD_Owner_Only.docx" }));
  expect(skipped.getByText("Owner-only file · owner only · Skipped: an owner-only file; upload it on its own")).toBeTruthy();
  expect(skipped.getByText("This file was not read.")).toBeTruthy();
  // member sections carry anchors in the member list's order, for links from the files card
  expect(screen.getByRole("region", { name: "ws/BD_Owner_Only.docx" }).id).toBe("m-2");
});

test("an owner-only plain file says so in its sub line and shows one section", () => {
  const passages: DocumentPassage[] = [
    { seq: 0, kind: "section", where: "page 1", heading: null, content: "Private notes." },
    { seq: 1, kind: "section", where: "page 2", heading: "Next steps", content: "Call Dana." },
  ];
  render(<DocumentView doc={doc({ name: "private.pdf", audience: "owner_only", passages: 2 })} members={[]} passages={passages} />);
  expect(screen.getByText("Ready · Owner only · 2 passages")).toBeTruthy();
  expect(screen.getAllByRole("region")).toHaveLength(1);
  expect(screen.getByText("page 1")).toBeTruthy();
  expect(screen.getByRole("heading", { level: 3, name: "Next steps" })).toBeTruthy();
  expect(screen.getByText("Call Dana.")).toBeTruthy();
  expect(screen.queryByText(/files inside/)).toBeNull();
});

test("a plain file with nothing read says so", () => {
  render(<DocumentView doc={doc({ name: "empty.txt", passages: 0, stage: "failed", status: "failed", message: "The file could not be read." })} members={[]} passages={[]} />);
  expect(screen.getByText("Failed")).toBeTruthy();
  expect(screen.getByText("Nothing was read from this file: The file could not be read.")).toBeTruthy();
});
