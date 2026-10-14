// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useApi } from "@/lib/useApi";

function ApiProbe({ path }: { path: string }) {
  const { data } = useApi<{ runs: unknown[] }>(path);
  return <p role="status">{data ? `Runs: ${data.runs.length}` : "Loading records"}</p>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("query changes hide prior results and abort the obsolete request", async () => {
  const signals: (AbortSignal | null)[] = [];
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    signals.push(init?.signal ?? null);
    const runs = String(input).includes("limit=8")
      ? []
      : [{ run_id: "run-1", domain: "example.com", status: "completed", label: "Completed", created_at: "2026-01-01T00:00:00Z" }];
    return Promise.resolve(new Response(JSON.stringify({ next_cursor: null, runs }), { status: 200 }));
  });
  vi.stubGlobal("fetch", fetch);

  const { rerender } = render(<ApiProbe path="/v1/runs?limit=8" />);
  await screen.findByText("Runs: 0");
  expect(signals[0]?.aborted).toBe(false);

  rerender(<ApiProbe path="/v1/runs?limit=9" />);
  expect(screen.getByRole("status").textContent).toBe("Loading records");
  expect(signals[0]?.aborted).toBe(true);
  await screen.findByText("Runs: 1");
});
