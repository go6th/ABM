// @vitest-environment jsdom
// A stopped run: the plain stop sentence, the kept counts, the missing areas, the collected
// findings, and Resume (one POST /v1/runs with resume_from, then the new run's page).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { canResume, stopExplanation, storedTotal } from "@/lib/run-stop";
import { CollectedView, MissingAreas, ResumeRun, StopReason, StoredSummary } from "@/components/ws/RunStopReport";
import type { RunCollected, StoredCounts } from "@/lib/gen/api.gen";

const push = vi.fn();
vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ push, refresh: vi.fn() }) }));
const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));

afterEach(() => { cleanup(); apiMock.mockReset(); push.mockReset(); });

const STORED: StoredCounts = { paid_calls: 120, cached_calls: 30, model_replies: 40, facts: 12, drafts: 1, deliverables: 0 };
const EMPTY: StoredCounts = { paid_calls: 0, cached_calls: 0, model_replies: 0, facts: 0, drafts: 0, deliverables: 0 };

test("every stop reason reads as a sentence, never as a raw code", () => {
  expect(stopExplanation(null)).toBeNull();
  expect(stopExplanation("worker_lost")).toMatch(/stopped unexpectedly/);
  expect(stopExplanation("ProviderCreditsExhausted")).toMatch(/ran out of credits/);
  expect(stopExplanation("build_failed:missing_narrative")).toMatch(/summary document/);
  expect(stopExplanation("SomethingNew")).toMatch(/stopped before it finished/);
  expect(stopExplanation("SomethingNew")).not.toContain("SomethingNew");
});

test("only a stopped run that kept something can be resumed", () => {
  expect(storedTotal(STORED)).toBe(163);
  expect(canResume("PARTIAL", STORED)).toBe(true);
  expect(canResume("FAILED", STORED)).toBe(true);
  expect(canResume("PARTIAL", EMPTY)).toBe(false);
  expect(canResume("COMPLETED", STORED)).toBe(false);
  expect(canResume("RUNNING", STORED)).toBe(false);
  expect(canResume("PARTIAL", null)).toBe(false);
});

test("the technical detail is shown only when asked for", () => {
  const { rerender } = render(<StopReason reason="RuntimeError" detail="RuntimeError: boom" showDetail={false} />);
  expect(screen.queryByText("RuntimeError: boom")).toBeNull();
  rerender(<StopReason reason="RuntimeError" detail="RuntimeError: boom" showDetail />);
  expect(screen.getByText("RuntimeError: boom")).toBeTruthy();
});

test("kept counts and missing areas are listed with their reasons", () => {
  render(<><StoredSummary stored={STORED} /><MissingAreas missing={[{ row: "job_board_by_lane", status: "Blocked", reason: "source_unavailable" }]} /></>);
  expect(screen.getByText("120")).toBeTruthy();
  expect(screen.getByText("plus 30 reused at no cost")).toBeTruthy();
  expect(screen.getByText("Job board by lane")).toBeTruthy();
  expect(screen.getByText("Source unavailable")).toBeTruthy();
});

test("collected findings render as plain text (model output stays inert)", () => {
  const data: RunCollected = {
    run_id: "r", status: "PARTIAL", reason: "worker_lost", stop_detail: null, stored: STORED,
    pulls: [{ tool_category: "web_search", calls: 10, cached: 4, retrieved_from: null, retrieved_to: null }],
    facts: [{ fact_id: "f1", attribute: "jobs.salesforce.build", value: "<img src=x onerror=alert(1)>", grade: "probable", lane: "salesforce", may_state_to_prospect: false }],
    drafts: { hypotheses: [], questions: [], narrative: [{ text: "Recent activity points to a platform investment.", fact_ids: ["f1"] }] },
    missing: [],
  };
  const { container } = render(<CollectedView data={data} />);
  expect(screen.getByText("Web search")).toBeTruthy();
  expect(screen.getByText("10 (4 reused)")).toBeTruthy();
  expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeTruthy();
  expect(container.querySelector("img")).toBeNull();
  expect(screen.getByText("Probable")).toBeTruthy();
  expect(screen.getByText("Recent activity points to a platform investment.")).toBeTruthy();
});

test("resume posts one run with resume_from and opens it", async () => {
  apiMock.mockResolvedValue({ run_id: "new-run", status: "QUEUED", replayed: false });
  render(<ResumeRun domain="acme.example" runId="old-run" />);
  fireEvent.click(screen.getAllByText("Resume research")[0]!);
  const confirm = await screen.findAllByText("Resume research");
  fireEvent.click(confirm[confirm.length - 1]!);
  fireEvent.click(confirm[confirm.length - 1]!); // a double click starts one run
  await waitFor(() => expect(push).toHaveBeenCalledWith("/runs/new-run"));
  expect(apiMock).toHaveBeenCalledTimes(1);
  const [method, path, body, headers] = apiMock.mock.calls[0]!;
  expect([method, path, body]).toEqual(["POST", "/v1/runs", { domain: "acme.example", resume_from: "old-run" }]);
  expect(Object.keys(headers as object)).toEqual(["Idempotency-Key"]);
});

test("a resume without credits says so", async () => {
  const { ApiError } = await import("@/lib/api");
  apiMock.mockRejectedValue(new ApiError(402, "insufficient_credits", "x"));
  render(<ResumeRun domain="acme.example" runId="old-run" />);
  fireEvent.click(screen.getAllByText("Resume research")[0]!);
  const confirm = await screen.findAllByText("Resume research");
  fireEvent.click(confirm[confirm.length - 1]!);
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Your workspace does not have enough credits to start a run.");
  expect(push).not.toHaveBeenCalled();
});

test("file mode outcomes read as sentences and the gate's problems are listed without text", async () => {
  const { GateDefects, RunStopReport } = await import("@/components/ws/RunStopReport");
  expect(stopExplanation("gate_failed")).toMatch(/quality check/);
  expect(stopExplanation("documents_not_ready")).toMatch(/Indexed/);
  render(<GateDefects gate={{ verdict: "fail", defects: [
    { check: "leak_check", item: "summary:ngram=2,embedding=0" },
    { check: "client_dashes", item: "summary:3" },
    { check: "client_dashes", item: "evidence:1" },
  ] }} />);
  expect(screen.getByText(/repeats your private uploaded material/)).toBeTruthy();
  expect(screen.getByText("2")).toBeTruthy();
  expect(document.body.textContent).not.toContain("ngram");
  cleanup();
  // a file run is never resumed as a public run
  render(<RunStopReport runId="r1" status="PARTIAL" reason="gate_failed" detail={null} stored={STORED} missing={[]}
    domain="acme.example" canStart isAdmin={false} fileMode gate={null} />);
  expect(screen.queryByText(/Resume research/)).toBeNull();
});
