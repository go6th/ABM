// @vitest-environment jsdom
// The token guard's pause (a run at 3x its estimate) asks one decision at a time: Continue or
// Stop, never "Continue 5 times" or "Auto continue".
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Checkpoint, overEstimate } from "@/components/ws/RunSteps";

vi.mock("@/lib/api", () => ({
  api: vi.fn(() => Promise.reject(new Error("offline"))),
  ApiError: class extends Error {},
}));

afterEach(cleanup);

test("overEstimate is the multiple of the estimate once at or past 3x, else null", () => {
  expect(overEstimate({ total: 3_000, expected_tokens: 1_000 })).toBe(3);
  expect(overEstimate({ total: 2_999, expected_tokens: 1_000 })).toBeNull();
  expect(overEstimate({ total: 9_000, expected_tokens: null })).toBeNull();
  expect(overEstimate(null)).toBeNull();
});

test("a token pause offers Continue and Stop only", () => {
  render(<Checkpoint runId="r1" status="AWAITING_APPROVAL" onDecided={() => undefined} auto={0} setAuto={() => undefined} tokens={{ total: 5_200_000, expected_tokens: 1_475_000 }} />);
  expect(screen.getByText("This run is well over its estimate")).toBeTruthy();
  expect(screen.getByText(/3\.5× the tokens/)).toBeTruthy();
  const names = screen.getAllByRole("button").map((b) => b.textContent);
  expect(names).toHaveLength(2);
  expect(names.join(" ")).not.toMatch(/5 times|Auto/);
});

test("an ordinary approval pause keeps its own panel", () => {
  render(<Checkpoint runId="r1" status="AWAITING_APPROVAL" onDecided={() => undefined} auto={0} setAuto={() => undefined} tokens={{ total: 100, expected_tokens: 1_000 }} />);
  expect(screen.queryByText("This run is well over its estimate")).toBeNull();
  expect(screen.getAllByRole("button").length).toBeGreaterThan(2);
});
