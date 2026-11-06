// @vitest-environment jsdom
// The import banner on the account page: nothing when every part finished, a warning naming the
// file and part otherwise, with a link to the Uploaded files sheet.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { ImportProblem } from "@/lib/gen/api.gen";
import { ImportProblems, describeProblem } from "@/components/accounts/ImportProblems";

afterEach(() => cleanup());

const failed: ImportProblem = { document_name: "Acme.zip", part: "jobs", status: "failed", rows_skipped: 0, reason: "IntegrityError (ck_job_postings_url)" };
const pending: ImportProblem = { document_name: "Acme.zip", part: "people", status: "pending", rows_skipped: 0, reason: null };

test("renders nothing when no part has a problem", () => {
  const { container } = render(<ImportProblems problems={[]} sheetHref="/accounts/acme.com/uploaded-files" />);
  expect(container.firstChild).toBeNull();
});

test("names the failed and the pending part and links to the sheet", () => {
  render(<ImportProblems problems={[failed, pending]} sheetHref="/accounts/acme.com/uploaded-files" />);
  const banner = screen.getByTestId("import-problems");
  expect(banner.textContent).toContain("Some parts of the latest file import did not finish");
  expect(banner.textContent).toContain("Job postings failed (Acme.zip)");
  expect(banner.textContent).toContain("People list still pending (Acme.zip)");
  expect(banner.className).toContain("error");
  expect(screen.getByRole("link").getAttribute("href")).toBe("/accounts/acme.com/uploaded-files");
});

test("a part still running is not called failed", () => {
  expect(describeProblem({ ...pending, status: "running", part: "tech" })).toBe("Tech stack and teams still running (Acme.zip)");
  render(<ImportProblems problems={[pending]} sheetHref="/x" />);
  expect(screen.getByTestId("import-problems").className).toContain("warn");
});
