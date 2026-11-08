// @vitest-environment jsdom
// Resume research is offered wherever the server says a run can be resumed (the account page's
// rule, sent as ``can_resume`` on the Research board and the run page): one button posts the
// resume, says how much is already done, and shows the server's reason when it is refused. It asks
// twice first ("Resume this research?", then "Are you sure?"); nothing posts before both.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

const api = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public readonly status: number, public readonly type: string, public readonly detail: string) {
      super(`${status} ${type}`);
    }
  }
  const errorText = (cause: unknown, fallback: string) => (cause instanceof ApiError && cause.detail?.trim() ? cause.detail : fallback);
  return { api: (...args: unknown[]) => api(...args), ApiError, errorText };
});

import { replayWarning, ResumeButton, ResumeResearch } from "@/components/accounts/ResumeResearch";
import { resumableRun } from "@/components/research/ResearchBoard";
import { ApiError } from "@/lib/api";

afterEach(() => {
  cleanup();
  api.mockReset();
});

/** Resume research, then both confirmations. */
function resumeTwice() {
  fireEvent.click(screen.getByRole("button", { name: "Resume research" }));
  fireEvent.click(screen.getByRole("button", { name: "Yes, resume" }));
  fireEvent.click(screen.getByRole("button", { name: "Yes, resume it" }));
}

const resumed = { job_id: "j2", resumes_run_id: "r1", domain: "acme.example", status: "queued", steps_done: 4, steps_total: 8 };

test("the button resumes the run and says how much is already done", async () => {
  api.mockResolvedValue(resumed);
  const onResumed = vi.fn();
  render(<ResumeButton runId="r1" compact onResumed={onResumed} />);
  resumeTwice();
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe(
    "Resuming: 4 of 8 steps already done. You will be notified when it finishes.",
  ));
  expect(api).toHaveBeenCalledWith("POST", "/v1/research/resume", { run_id: "r1", request_id: expect.any(String), confirm: true, confirm_again: true });
  expect(onResumed).toHaveBeenCalledWith(resumed);
});

test("a refused resume shows the server's reason and keeps the button", async () => {
  api.mockRejectedValue(new ApiError(409, "job_open", "This account already has a research running."));
  render(<ResumeButton runId="r1" />);
  resumeTwice();
  await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("This account already has a research running."));
  expect((screen.getByRole("button", { name: "Resume research" }) as HTMLButtonElement).disabled).toBe(false);
});

test("the account card still explains the stop and becomes the resuming line", async () => {
  api.mockResolvedValue(resumed);
  render(<ResumeResearch stopped={{ run_id: "r1", stalled: false, reason: "The worker was lost.", steps_done: 4, steps_total: 8 }} />);
  expect(screen.getByText("This research stopped before finishing")).toBeTruthy();
  resumeTwice();
  await waitFor(() => expect(screen.queryByText("This research stopped before finishing")).toBeNull());
  expect(screen.getByRole("status").textContent).toContain("Resuming: 4 of 8 steps already done");
});

test("a run keyed by an older replay-key version warns what the resume may cost before it is confirmed", async () => {
  api.mockResolvedValue(resumed);
  render(<ResumeResearch stopped={{ run_id: "r1", stalled: false, reason: "The worker was lost.", steps_done: 4, steps_total: 8, replay_extra_usd: 12.5 }} />);
  expect(screen.getByRole("note").textContent).toContain("This resume may cost up to $12.50 more");
  expect(screen.queryByText(/reuses it at no cost/)).toBeNull();
  expect(api).not.toHaveBeenCalled(); // shown before the user confirms with the button
  resumeTwice();
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
});

test("nothing is resumed until both questions are answered yes; No at either one posts nothing", () => {
  render(<ResumeButton runId="r1" />);
  fireEvent.click(screen.getByRole("button", { name: "Resume research" }));
  expect(screen.getByText(/Resume this research\?/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.click(screen.getByRole("button", { name: "Resume research" }));
  fireEvent.click(screen.getByRole("button", { name: "Yes, resume" }));
  expect(screen.getByText(/Are you sure\?/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Resume research" })).toBeTruthy();
  expect(api).not.toHaveBeenCalled();
});

test("current replay keys show no warning", () => {
  expect(replayWarning(null)).toBeNull();
  expect(replayWarning(0)).toBeNull();
  render(<ResumeResearch stopped={{ run_id: "r1", stalled: false, reason: "The worker was lost.", steps_done: 4, steps_total: 8, replay_extra_usd: null }} />);
  expect(screen.queryByRole("note")).toBeNull();
  expect(screen.getByText(/reuses it at no cost/)).toBeTruthy();
});

test("the board offers Resume only on an ended item the server marks resumable", () => {
  const item = { open: false, can_resume: true, run_id: "r1" } as Parameters<typeof resumableRun>[0];
  expect(resumableRun(item)).toBe("r1");
  expect(resumableRun({ ...item!, can_resume: false })).toBeNull();
  expect(resumableRun({ ...item!, can_resume: undefined })).toBeNull(); // an older server
  expect(resumableRun({ ...item!, open: true })).toBeNull();
  expect(resumableRun({ ...item!, run_id: null })).toBeNull();
  expect(resumableRun(undefined)).toBeNull();
});
