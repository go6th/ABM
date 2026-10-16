// @vitest-environment jsdom
// The account page lists the files uploaded for it (they were stored but shown nowhere).
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { UploadedFilesCard } from "@/components/accounts/UploadedFilesCard";
import type { DocumentStatus } from "@/lib/gen/api.gen";

afterEach(cleanup);

const doc = (over: Partial<DocumentStatus>): DocumentStatus => ({
  document_id: "d1", account_id: "a1", name: "Workday_People_List.xlsx", status: "indexed", stage: "indexed", declared_type: "xlsx",
  detected_type: "xlsx", size_bytes: 59392, reason: null, message: null, passages: 420, redactions: 0, redaction_tier: "standard",
  authoritative: false, company: null, needs_company: false, created_at: "2026-10-15T00:00:00Z", parts_total: 1, parts_done: 1, parts_failed: [], indexed_passages: 0, members_read: 0, members_skipped: [],
  ...over,
});

test("each uploaded file shows its name, size, passages and status", () => {
  render(<UploadedFilesCard docs={[doc({}), doc({ document_id: "d2", name: "Tech.xlsx", stage: "parsed", status: "parsed", passages: 0 })]} />);
  expect(screen.getByText("Workday_People_List.xlsx")).toBeTruthy();
  expect(screen.getByText(/420 passages/)).toBeTruthy();
  expect(screen.getByText("Ready")).toBeTruthy();
  expect(screen.getByText("Indexing")).toBeTruthy(); // bound to the account, on its way to Ready
  expect(screen.getByRole("link", { name: "Add files" }).getAttribute("href")).toBe("/research/new?tab=file");
});

test("with no uploads the card says where they will appear", () => {
  render(<UploadedFilesCard docs={[]} />);
  expect(screen.getByText("Files you upload for this account appear here.")).toBeTruthy();
});
