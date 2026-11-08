// @vitest-environment jsdom
// The account and run screens update themselves without hammering the server (2026-11-07): a
// queued research is checked with one small read and the page is drawn again only when it started,
// a finished step re-draws the overview at most every 30 seconds, a failed poll keeps the run's
// steps and buttons on screen, the file analysis is polled on the client with a backoff and stops
// at an approval, and the Research board's reader stops when signed out. SYNTHETIC data only.
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const refresh = vi.fn();
const router = { refresh, push: vi.fn(), replace: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));
const runState = { current: null as unknown };
vi.mock("@/components/ws/RunSteps", async (original) => ({
  ...(await original<typeof import("@/components/ws/RunSteps")>()),
  useRun: () => runState.current,
}));

import { CREDIT_CHECK_MS, QUEUED_CHECK_MS, ResearchProgress, STEP_REFRESH_GAP_MS } from "@/components/accounts/ResearchProgress";
import { FileAnalysis } from "@/components/ws/FileAnalysis";
import { RunPanel } from "@/components/ws/RunSteps";
import { ApiError } from "@/lib/api";

const setVisibility = (state: "visible" | "hidden") => {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
};
const flush = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

beforeEach(() => { vi.useFakeTimers(); refresh.mockReset(); apiMock.mockReset(); setVisibility("visible"); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const item = (over: Record<string, unknown> = {}) => ({ job_id: "job-1", run_id: null, status: "queued", ...over });
const snapshot = (...running: unknown[]) => ({ as_of: "2026-11-07T10:00:00Z", detail: null, running, recent: [] });
const quiet = { run: null, progress: null, error: null, status: "", terminal: false, reload: vi.fn() };

// --- a research that has not started ----------------------------------------------------------

test("a queued research is checked with one small read, not a page reload every 10 seconds", async () => {
  runState.current = quiet;
  apiMock.mockResolvedValue(snapshot(item()));
  render(<ResearchProgress job={{ id: "job-1", kind: "full", scope: "full", status: "queued" }} scopeName="Full research" />);
  await flush(QUEUED_CHECK_MS * 5);
  expect(refresh).not.toHaveBeenCalled(); // nothing changed: the page is not drawn again
  expect(apiMock).toHaveBeenCalledTimes(5);
  expect(apiMock).toHaveBeenCalledWith("GET", "/v1/research/live?selected=", undefined, {}, expect.any(AbortSignal));
  apiMock.mockResolvedValue(snapshot(item({ run_id: "run-9", status: "running" })));
  await flush(QUEUED_CHECK_MS);
  expect(refresh).toHaveBeenCalledTimes(1); // it started: the page is drawn once
});

test("a research waiting for credit is checked once a minute, and a hidden tab is not checked", async () => {
  runState.current = quiet;
  apiMock.mockResolvedValue(snapshot(item({ status: "waiting_credit_limit" })));
  render(<ResearchProgress job={{ id: "job-1", kind: "full", scope: "full", status: "waiting_credit_limit" }} scopeName="Full research" />);
  await flush(CREDIT_CHECK_MS * 3);
  expect(apiMock).toHaveBeenCalledTimes(3);
  await act(async () => { setVisibility("hidden"); });
  await flush(CREDIT_CHECK_MS * 5);
  expect(apiMock).toHaveBeenCalledTimes(3);
  await act(async () => { setVisibility("visible"); await vi.advanceTimersByTimeAsync(0); });
  expect(apiMock).toHaveBeenCalledTimes(4); // read once when the tab comes back
});

test("the card has no Refresh button, one link to the run and no leave-the-page line", () => {
  runState.current = { ...quiet, run: { status: "RUNNING" }, progress: { stages_done: 2, stages_total: 8, finished: false, label: "x", steps: [{ id: "a", name: "Set up", state: "done" }, { id: "b", name: "Reading", state: "running" }] }, status: "RUNNING" };
  apiMock.mockResolvedValue(snapshot());
  render(<ResearchProgress job={{ id: "job-1", run_id: "run-1", kind: "full", scope: "full", status: "running" }} scopeName="Full research" />);
  expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
  expect(screen.queryByText(/You can leave this page/)).toBeNull();
  expect(screen.getAllByRole("link").map((l) => l.textContent)).toEqual(["Run details"]);
  expect(screen.queryByRole("link", { name: "Watch it live" })).toBeNull();
});

// --- the overview follows the run's steps ------------------------------------------------------

test("a finished step re-draws the overview, at most every 30 seconds, and only while the tab is shown", async () => {
  const progress = (done: number) => ({ stages_done: done, stages_total: 8, finished: false, label: "x", steps: [{ id: "a", name: "Set up", state: "running" }] });
  const job = { id: "job-1", run_id: "run-1", kind: "full", scope: "full", status: "running" };
  const view = (done: number) => ({ ...quiet, run: { status: "RUNNING" }, progress: progress(done), status: "RUNNING" });
  runState.current = view(1);
  const { rerender } = render(<ResearchProgress job={job} scopeName="x" />);
  await flush(1000);
  expect(refresh).not.toHaveBeenCalled(); // the first reading is not a change
  runState.current = view(2);
  rerender(<ResearchProgress job={job} scopeName="x" />);
  await flush(0);
  expect(refresh).toHaveBeenCalledTimes(1);
  runState.current = view(3);
  rerender(<ResearchProgress job={job} scopeName="x" />);
  await flush(STEP_REFRESH_GAP_MS - 1000);
  expect(refresh).toHaveBeenCalledTimes(1); // too soon: it waits
  await flush(1000);
  expect(refresh).toHaveBeenCalledTimes(2);
  await act(async () => { setVisibility("hidden"); });
  runState.current = view(4);
  rerender(<ResearchProgress job={job} scopeName="x" />);
  await flush(STEP_REFRESH_GAP_MS * 2);
  expect(refresh).toHaveBeenCalledTimes(2); // hidden: nothing
  await act(async () => { setVisibility("visible"); });
  await flush(STEP_REFRESH_GAP_MS);
  expect(refresh).toHaveBeenCalledTimes(3);
});

// --- the run panel ----------------------------------------------------------------------------

const loaded = {
  run: { status: "RUNNING", token_use: null },
  progress: { stages_done: 2, stages_total: 8, finished: false, label: "x", steps: [{ id: "a", name: "Set up", state: "done" }, { id: "b", name: "Reading", state: "running" }] },
  status: "RUNNING",
  terminal: false,
  reload: vi.fn(),
};

test("one failed read keeps the steps and Stop on screen and says it is reconnecting", () => {
  const state = { ...loaded, error: new ApiError(500, "http_500", "Internal Server Error") };
  render(<RunPanel runId="run-1" state={state as never} />);
  expect(screen.getByText("Reading")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Stop and keep results" })).toBeTruthy();
  expect(screen.getByText("Reconnecting…")).toBeTruthy();
  expect(screen.queryByText(/Internal Server Error/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
});

test("an error shows only when nothing has loaded, and a missing run says so", () => {
  render(<RunPanel runId="run-1" state={{ ...quiet, error: new ApiError(500, "http_500", "") } as never} />);
  expect(screen.queryByText("Reconnecting…")).toBeNull();
  expect(screen.getByText(/could not|try/i)).toBeTruthy();
  cleanup();
  render(<RunPanel runId="run-1" state={{ ...loaded, error: new ApiError(404, "not_found", "") } as never} />);
  expect(screen.getByText("This run was not found in your workspace.")).toBeTruthy();
});

test("how a run ended is said the same way the token card says it", () => {
  const ended = (status: string) => ({ ...loaded, run: { status, token_use: null }, status, terminal: true, error: null });
  render(<RunPanel runId="run-1" state={ended("BUDGET_EXCEEDED") as never} />);
  expect(screen.getByText(/Stopped: it reached its limit/)).toBeTruthy();
  expect(screen.queryByText("Completed with partial results")).toBeNull();
  cleanup();
  render(<RunPanel runId="run-1" state={ended("PARTIAL") as never} />);
  expect(screen.getByText("Completed with partial results")).toBeTruthy();
  cleanup();
  render(<RunPanel runId="run-1" state={ended("FAILED") as never} />);
  expect(screen.getByText(/stopped before it finished/)).toBeTruthy();
});

// --- the file analysis -------------------------------------------------------------------------

const analysis = (over: Record<string, unknown> = {}) => ({
  account_name: "Synthetic Ltd", created_at: "2026-11-07T09:00:00Z", done: false, files: 1, finished_at: null,
  public_run_id: null, run_id: "11111111-1111-1111-1111-111111111111", status: "RUNNING", ...over,
});

test("the analysis is read on the client with a backoff, the page is drawn once when it changes", async () => {
  apiMock.mockResolvedValue(analysis());
  render(<FileAnalysis domain="x.example" run={analysis() as never} />);
  await flush(5000); // the first read after 5 s, then 10 s, 20 s, 30 s
  expect(apiMock).toHaveBeenCalledTimes(1);
  expect(apiMock).toHaveBeenCalledWith("GET", "/v1/accounts/x.example/file-analysis?run=11111111-1111-1111-1111-111111111111", undefined, {}, expect.any(AbortSignal));
  await flush(9000);
  expect(apiMock).toHaveBeenCalledTimes(1);
  await flush(1000);
  expect(apiMock).toHaveBeenCalledTimes(2);
  await flush(20_000);
  expect(apiMock).toHaveBeenCalledTimes(3);
  await flush(30_000);
  expect(apiMock).toHaveBeenCalledTimes(4);
  expect(refresh).not.toHaveBeenCalled();
  apiMock.mockResolvedValue(analysis({ done: true, status: "COMPLETED" }));
  await flush(30_000);
  expect(refresh).toHaveBeenCalledTimes(1);
});

test("an analysis that waits for approval, or has stopped, is not polled and says so", async () => {
  render(<FileAnalysis domain="x.example" run={analysis({ status: "AWAITING_APPROVAL" }) as never} />);
  await flush(120_000);
  expect(apiMock).not.toHaveBeenCalled();
  expect(refresh).not.toHaveBeenCalled();
  expect(screen.getByText("Waiting for your approval")).toBeTruthy();
  cleanup();
  render(<FileAnalysis domain="x.example" run={analysis({ status: "FAILED" }) as never} />);
  await flush(120_000);
  expect(apiMock).not.toHaveBeenCalled();
  expect(screen.getByText("The analysis stopped before it finished")).toBeTruthy();
});

test("a hidden tab does not read the analysis", async () => {
  apiMock.mockResolvedValue(analysis());
  render(<FileAnalysis domain="x.example" run={analysis() as never} />);
  await act(async () => { setVisibility("hidden"); });
  await flush(120_000);
  expect(apiMock).not.toHaveBeenCalled();
});
