// @vitest-environment jsdom
// The token meter beside Ask: the usage the stream reports, the context ring against the model's
// window, and the panel with the conversation's tokens and cost.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ContextMeter } from "@/components/ask/ContextMeter";
import { contextTokens, sumUsage, usageFromDone, usageFromTurn, type TurnUsage } from "@/lib/chat-usage";

vi.mock("@/lib/api", () => ({
  api: vi.fn(() => Promise.resolve({
    default_model: "claude-opus-5-5",
    models: [{ id: "claude-opus-5-5", display_name: "Opus 5.5", context_window: 200_000, provider: "anthropic" }],
  })),
}));

afterEach(cleanup);

const first: TurnUsage = { input: 1_200, output: 300, cacheRead: 40_000, cacheWrite: 0, costUsd: 0.02, model: "claude-opus-5-5" };
const second: TurnUsage = { input: 900, output: 500, cacheRead: 60_000, cacheWrite: 2_000, costUsd: 0.03, model: "claude-opus-5-5" };

test("a finished answer's frame carries its tokens and cost; a replayed one carries none", () => {
  expect(usageFromDone({ turn_id: "t", input_tokens: 1200, output_tokens: 300, cache_read_tokens: 40000, cache_write_tokens: 0, cost_usd: "0.0200", model: "claude-opus-5-5", replayed: false })).toEqual({
    input: 1200, output: 300, cacheRead: 40000, cacheWrite: 0, costUsd: 0.02, model: "claude-opus-5-5",
  });
  expect(usageFromDone({ turn_id: "t", replayed: true })).toBeUndefined();
  expect(usageFromDone({ input_tokens: -5, output_tokens: "x", cost_usd: "nan" })).toEqual({
    input: 0, output: 0, cacheRead: 0, cacheWrite: 0, costUsd: 0, model: null,
  });
});

test("a stored turn gives the same figures as the stream", () => {
  expect(usageFromTurn({ input_tokens: 1200, output_tokens: 300, cache_read_tokens: 40000, cache_write_tokens: 0, cost_usd: "0.02", model_id: "claude-opus-5-5" })).toEqual(first);
});

test("the context is the whole input the model read; the conversation adds up every answer", () => {
  expect(contextTokens(second)).toBe(62_900);
  expect(sumUsage([first, second])).toEqual({ input: 2_100, output: 800, cacheRead: 100_000, cacheWrite: 2_000, costUsd: 0.05, model: null });
});

test("the ring shows the share of the model's window the latest answer used", async () => {
  render(<ContextMeter usages={[first, second]} modelId="claude-opus-5-5" />);
  expect(await screen.findByRole("button", { name: /Token use: 31% of the context used/ })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Token use/ }));
  const panel = screen.getByRole("dialog", { name: "Token use" });
  expect(panel.textContent).toContain("63K of 200K tokens");
  expect(panel.textContent).toContain("Cost so far");
  expect(panel.textContent).toContain("$0.05");
  expect(panel.textContent).toContain("Opus 5.5");
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("nothing shows before the conversation has an answer", () => {
  const { container } = render(<ContextMeter usages={[]} modelId={null} />);
  expect(container.textContent).toBe("");
});
