// @vitest-environment jsdom
// New research takes an optional research goal (owner decision 2026-10-23): sent only when the
// user wrote one, 500 characters at most with a counter, and a goal under 3 characters blocks
// Start research.
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
vi.mock("@/components/Limits", () => ({
  LimitNote: () => null,
  LimitReached: () => null,
  limitLine: () => "",
  limitReached: () => null,
  useMyLimits: () => ({ limits: null, reload: vi.fn() }),
}));
vi.mock("@/components/ModelMenu", () => ({
  ResearchModelChips: () => null,
  useResearchModels: () => ({ body: {} }),
}));

import { ResearchCompany } from "@/components/accounts/ResearchCompany";
import type { ResearchEstimate } from "@/lib/gen/api.gen";

afterEach(() => {
  cleanup();
  api.mockReset();
});

const estimate = { minutes: 30, usage: { tokens: 1000, cost_usd: 1, runs: 0 } } as unknown as ResearchEstimate;
const started = { job_id: "j1", status: "queued", credits: 1, domain: "acme.example" };

function fill(goal?: string) {
  render(<ResearchCompany estimate={estimate} onStarted={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Company name"), { target: { value: "Acme" } });
  fireEvent.change(screen.getByLabelText("Website"), { target: { value: "acme.example" } });
  if (goal !== undefined) fireEvent.change(screen.getByLabelText("Research goal (optional)"), { target: { value: goal } });
}

async function proceed() {
  fireEvent.click(screen.getByRole("button", { name: "Start research" }));
  fireEvent.click(screen.getByRole("button", { name: "Proceed" }));
  await waitFor(() => expect(api).toHaveBeenCalled());
  return api.mock.calls[0]?.[2] as Record<string, unknown>;
}

test("a written goal is sent trimmed, with a counter of its 500 characters", async () => {
  api.mockResolvedValue(started);
  fill("  their HR systems ");
  expect(screen.getByText("19/500")).toBeTruthy();
  expect((screen.getByLabelText("Research goal (optional)") as HTMLTextAreaElement).maxLength).toBe(500);
  const body = await proceed();
  expect(body.goal).toBe("their HR systems");
});

test("no goal is sent when the box is empty", async () => {
  api.mockResolvedValue(started);
  fill("   ");
  const body = await proceed();
  expect("goal" in body).toBe(false);
});

test("a goal under three characters blocks Start research", () => {
  fill("ab");
  expect((screen.getByRole("button", { name: "Start research" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("Describe the goal in a few words.")).toBeTruthy();
});
