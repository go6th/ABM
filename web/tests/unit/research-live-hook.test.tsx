// @vitest-environment jsdom
// The Research board's reader when the stream cannot be opened (2026-11-07): the snapshot is read
// one at a time, slowing to 30 s while it keeps failing, a signed-out session ends it, and a hidden
// tab holds no stream. SYNTHETIC data only.
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));

import { ApiError } from "@/lib/api";
import { useResearchLive } from "@/lib/useResearchLive";

// an event stream that always fails to open (the browser has closed it: readyState 2)
const opened: FakeSource[] = [];
class FakeSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  readyState = 2;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    opened.push(this);
    queueMicrotask(() => this.onerror?.());
  }
  addEventListener() { /* no events arrive */ }
  close() { this.closed = true; }
}

const setVisibility = (state: "visible" | "hidden") => {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
};
const flush = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

beforeEach(() => {
  vi.useFakeTimers();
  apiMock.mockReset();
  opened.length = 0;
  vi.stubGlobal("EventSource", FakeSource);
  setVisibility("visible");
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

test("an outage is read one request at a time and slows down to 30 seconds", async () => {
  apiMock.mockRejectedValue(new ApiError(503, "http_503", ""));
  renderHook(() => useResearchLive(null, null));
  await flush(0);
  expect(apiMock).toHaveBeenCalledTimes(1);
  await flush(2000);
  expect(apiMock).toHaveBeenCalledTimes(2);
  await flush(2000); // the next wait is 4 s
  expect(apiMock).toHaveBeenCalledTimes(2);
  await flush(2000);
  expect(apiMock).toHaveBeenCalledTimes(3);
  await flush(10 * 60_000);
  // after the backoff reaches 30 s it never goes faster: at most 20 reads in 10 minutes
  expect(apiMock.mock.calls.length).toBeLessThanOrEqual(3 + 20 + 1);
});

test("a slow read is not asked again while it has not answered", async () => {
  apiMock.mockImplementation(() => new Promise(() => undefined));
  renderHook(() => useResearchLive(null, null));
  await flush(60_000);
  expect(apiMock).toHaveBeenCalledTimes(1);
});

test("a signed-out session stops the reading and the reconnecting for good", async () => {
  apiMock.mockRejectedValue(new ApiError(401, "unauthorized", ""));
  renderHook(() => useResearchLive(null, null));
  await flush(0);
  const streams = opened.length;
  await flush(10 * 60_000);
  expect(apiMock).toHaveBeenCalledTimes(1);
  expect(opened.length).toBe(streams);
});

test("a hidden tab holds no stream and reads nothing; it opens again when shown", async () => {
  apiMock.mockRejectedValue(new ApiError(503, "http_503", ""));
  renderHook(() => useResearchLive(null, null));
  await flush(0);
  await act(async () => { setVisibility("hidden"); });
  const reads = apiMock.mock.calls.length;
  const streams = opened.length;
  await flush(10 * 60_000);
  expect(apiMock.mock.calls.length).toBe(reads);
  expect(opened.length).toBe(streams);
  await act(async () => { setVisibility("visible"); });
  expect(opened.length).toBe(streams + 1);
});
