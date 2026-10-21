// @vitest-environment jsdom
// The research token card: the exact total with the run's state, rate or run time, model cost and
// the share of what this kind of research used before; the bar against marks; the eight steps with
// their tokens and share; and the empty state before any model call.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { TokenMeter, formatTokens, meterState, scaleFor } from "@/components/ws/TokenMeter";
import type { RunStatusView, TokenUse } from "@/lib/gen/api.gen";

afterEach(cleanup);

const STEPS = [
  ["setup", "Set up"], ["search", "Web research"], ["read", "Reading and grading"], ["follow_up", "Follow-up searches"],
  ["write", "Writing up"], ["build", "Building files"], ["gate", "Quality check"], ["deliver", "Delivery"],
] as const;

function runWith(use: TokenUse, status = "running"): RunStatusView {
  return {
    run_id: "r1", status, reason: null, coverage: { rows: [], tally: [] }, tokens_used: use.total, token_use: use,
  } as unknown as RunStatusView;
}

const tokens = [4_200, 0, 206_300, 61_400, 0, 0, 0, 0];
const live: TokenUse = {
  total: 271_900, reused: 0, cost_usd: 1.69, expected_tokens: 500_000, expected_runs: 3,
  started_at: "2026-10-21T10:00:00Z", finished_at: null,
  steps: STEPS.map(([id, name], i) => ({ id, name, tokens: tokens[i]! })),
};
const states = STEPS.map(([id, name], i) => ({ id, name, state: i < 3 ? "done" : i === 3 ? "running" : "waiting" }));

test("a running research shows its total, state, next mark, cost and share of expected", () => {
  render(<TokenMeter run={runWith(live)} steps={states} at={{ index: 3, total: 8 }} status="RUNNING" />);
  const card = screen.getByRole("group", { name: "Token usage of this research" });
  expect(card.textContent).toContain("271,900tokens");
  expect(card.textContent).toContain("Running · step 4 of 8");
  expect(card.textContent).toContain("Next mark at 300K · 28.1K to go");
  expect(card.textContent).toContain("$1.69");
  expect(card.textContent).toContain("54%of expected");
});

test("every step lists its tokens and share; a done step without model calls says 0, a waiting one a dash", () => {
  render(<TokenMeter run={runWith(live)} steps={states} at={{ index: 3, total: 8 }} status="RUNNING" />);
  const rows = within(screen.getByRole("list", { name: "Tokens by step" })).getAllByRole("listitem");
  expect(rows.map((r) => r.textContent)).toEqual([
    "Set up4.2K2%", "Web research0", "Reading and grading206.3K76%", "Follow-up searches61.4K23%",
    "Writing up—", "Building files—", "Quality check—", "Delivery—",
  ]);
});

test("the bar fills one segment per step that used tokens, against even marks", () => {
  const { container } = render(<TokenMeter run={runWith(live)} steps={states} status="RUNNING" />);
  const segs = [...container.querySelectorAll(".tk-seg")];
  expect(segs.map((s) => s.getAttribute("class"))).toEqual(["tk-seg tk-c-setup", "tk-seg tk-c-read", "tk-seg tk-c-follow_up"]);
  expect(segs.map((s) => Number(s.getAttribute("x")))).toEqual([0, 4_200, 210_500]);
  expect(container.querySelector(".tk-bar svg")?.getAttribute("viewBox")).toBe("0 0 500000 12");
  expect([...container.querySelectorAll(".tk-marks span")].map((s) => s.textContent)).toEqual(["0", "100K", "200K", "300K", "400K", "500K"]);
});

test("a finished research shows its run time and final count", () => {
  const done = { ...live, finished_at: "2026-10-21T10:06:12Z" };
  render(<TokenMeter run={runWith(done, "completed")} steps={states.map((s) => ({ ...s, state: "done" }))} status="COMPLETED" />);
  const card = screen.getByRole("group", { name: "Token usage of this research" });
  expect(card.textContent).toContain("Finished");
  expect(card.textContent).toContain("6m 12s");
  expect(card.textContent).toContain("Run time");
  expect(card.textContent).toContain("Input and output tokens the model served");
});

test("before any model call the card shows the empty state, and says what was reused for free", () => {
  const none: TokenUse = { total: 0, reused: 120_000, steps: [], started_at: "2026-10-21T10:00:00Z" };
  render(<TokenMeter run={runWith(none)} steps={states} status="RUNNING" />);
  expect(screen.getByText("No tokens used yet")).toBeTruthy();
  expect(screen.getByText(/120K tokens were reused from earlier research so far, at no cost/)).toBeTruthy();
});

test("an older API without the token card still shows the total as one segment", () => {
  const old = { run_id: "r1", status: "running", reason: null, coverage: { rows: [], tally: [] }, tokens_used: 150_000 } as unknown as RunStatusView;
  const { container } = render(<TokenMeter run={old} status="RUNNING" />);
  expect(container.textContent).toContain("150,000tokens");
  expect([...container.querySelectorAll(".tk-seg")].map((s) => s.getAttribute("class"))).toEqual(["tk-seg tk-c-all"]);
});

test("helpers: compact counts, the bar scale and the run state", () => {
  expect(formatTokens(950)).toBe("950");
  expect(formatTokens(12_400)).toBe("12.4K");
  expect(formatTokens(300_000)).toBe("300K");
  expect(formatTokens(2_500_000)).toBe("2.5M");
  expect(scaleFor(0, null)).toEqual({ max: 100_000, marks: [0, 100_000] });
  expect(scaleFor(1_234_567, 900_000)).toEqual({ max: 1_250_000, marks: [0, 250_000, 500_000, 750_000, 1_000_000, 1_250_000] });
  expect(meterState("RUNNING", false)).toBe("waiting");
  expect(meterState("AWAITING_APPROVAL", true)).toBe("paused");
  expect(meterState("FAILED", true)).toBe("stopped");
  expect(meterState("PARTIAL", true)).toBe("finished");
});
