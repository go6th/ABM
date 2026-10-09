// @vitest-environment jsdom
import { cleanup, render, screen, act } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { RunProgress } from "@/components/Progress";

class FakeEventSource {
  static current: FakeEventSource | null = null;
  closed = false;
  private listeners = new Map<string, (event: MessageEvent<string>) => void>();

  constructor(_url: string) {
    void _url;
    FakeEventSource.current = this;
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    if (typeof listener === "function") {
      this.listeners.set(type, listener as (event: MessageEvent<string>) => void);
    }
  }

  close() {
    this.closed = true;
  }

  emit(type: string, data: unknown) {
    this.listeners.get(type)?.(new MessageEvent(type, { data: JSON.stringify(data) }));
  }
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  FakeEventSource.current = null;
});

test("shows run progress from the same-origin event stream and closes at completion", async () => {
  const onFinished = vi.fn();
  vi.stubGlobal("EventSource", FakeEventSource);

  render(<RunProgress runId="run-1" onFinished={onFinished} />);
  expect(screen.getByText("Checking progress")).toBeTruthy();
  expect(FakeEventSource.current).not.toBeNull();

  await act(async () => {
    FakeEventSource.current?.emit("progress", {
      label: "Completed",
      text: "4 of 4",
      stages_done: 4,
      stages_total: 4,
      current_stage: null,
      finished: true,
    });
  });

  expect(screen.getByText("Completed")).toBeTruthy();
  expect(screen.getByText("4 of 4 stages")).toBeTruthy();
  expect(onFinished).toHaveBeenCalledOnce();
  expect(FakeEventSource.current?.closed).toBe(true);
});

test("falls back to same-origin ETag polling when EventSource is unavailable", async () => {
  vi.stubGlobal("EventSource", undefined);
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    label: "Running",
    text: "2 of 4",
    stages_done: 2,
    stages_total: 4,
    current_stage: "research",
    finished: false,
  }), { status: 200, headers: { ETag: '"run-v2"' } }));
  vi.stubGlobal("fetch", fetch);

  render(<RunProgress runId="run-2" />);
  await screen.findByText("2 of 4 stages, now: research");

  expect(fetch).toHaveBeenCalledWith("/v1/runs/run-2/progress", expect.objectContaining({
    cache: "no-store",
    headers: {},
  }));
});
