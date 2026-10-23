// @vitest-environment jsdom
// Resume research is offered wherever the server says a run can be resumed (the account page's
// rule, sent as ``can_resume`` on the Research board and the run page): one button posts the
// resume, says how much is already done, and shows the server's reason when it is refused.
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
  return { api: (...args: unknown[]) => api(...args), ApiError };
});

import { ResumeButton, ResumeResearch } from "@/components/accounts/ResumeResearch";
import { resumableRun } from "@/components/research/ResearchBoard";
import { ApiError } from "@/lib/api";

afterEach(() => {
  cleanup();
  api.mockReset();
});

const resumed = { job_id: "j2", resumes_run_id: "r1", domain: "acme.example", status: "queued", steps_done: 4, steps_total: 8 };

test("the button resumes the run and says how much is already done", async () => {
  api.mockResolvedValue(resumed);
  const onResumed = vi.fn();
  render(<ResumeButton runId="r1" compact onResumed={onResumed} />);
  fireEvent.click(screen.getByRole("button", { name: "Resume research" }));
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe(
    "Resuming: 4 of 8 steps already done. You will be notified when it finishes.",
  ));
  expect(api).toHaveBeenCalledWith("POST", "/v1/research/resume", { run_id: "r1", request_id: expect.any(String) });
  expect(onResumed).toHaveBeenCalledWith(resumed);
});

test("a refused resume shows the server's reason and keeps the button", async () => {
  api.mockRejectedValue(new ApiError(409, "job_open", "This account already has a research running."));
  render(<ResumeButton runId="r1" />);
  fireEvent.click(screen.getByRole("button", { name: "Resume research" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("This account already has a research running."));
  expect((screen.getByRole("button", { name: "Resume research" }) as HTMLButtonElement).disabled).toBe(false);
});

test("the account card still explains the stop and becomes the resuming line", async () => {
  api.mockResolvedValue(resumed);
  render(<ResumeResearch stopped={{ run_id: "r1", stalled: false, reason: "The worker was lost.", steps_done: 4, steps_total: 8 }} />);
  expect(screen.getByText("This research stopped before finishing")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Resume research" }));
  await waitFor(() => expect(screen.queryByText("This research stopped before finishing")).toBeNull());
  expect(screen.getByRole("status").textContent).toContain("Resuming: 4 of 8 steps already done");
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
