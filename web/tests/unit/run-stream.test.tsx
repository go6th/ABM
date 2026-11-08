// @vitest-environment jsdom
// Following an open run without an event stream (jsdom has none): the progress is read at once
// and again every POLL_PROGRESS_MS after the last read finished, the run record is re-read every
// POLL_RUN_MS the same way, a failed read backs off, a missing run (404) or ended session (401)
// stops everything, a hidden tab reads nothing and reads again when it is shown, and nothing runs
// for an ended run or after unmount.
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));

import { ApiError } from "@/lib/api";
import { POLL_MAX_MS, POLL_PROGRESS_MS, POLL_RUN_MS, backoff, useRunStream } from "@/lib/useRunStream";

const PROGRESS = ["GET", "/v1/runs/run-1/progress", undefined, {}, expect.any(AbortSignal)];

const show = (state: "visible" | "hidden") => {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
};

beforeEach(() => { vi.useFakeTimers(); apiMock.mockReset(); apiMock.mockResolvedValue({ stages_done: 1, stages_total: 8, finished: false }); show("visible"); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

test("polls progress and the run record while the run is open, and stops on unmount", async () => {
  const onProgress = vi.fn();
  const reload = vi.fn();
  const { unmount } = renderHook(() => useRunStream("run-1", true, onProgress, reload));
  await vi.advanceTimersByTimeAsync(0);
  expect(apiMock).toHaveBeenCalledWith(...PROGRESS);
  expect(onProgress).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS);
  expect(onProgress).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(POLL_RUN_MS * 2 - POLL_PROGRESS_MS);
  expect(reload).toHaveBeenCalledTimes(2);
  const reads = onProgress.mock.calls.length;
  unmount();
  await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS * 3);
  expect(onProgress).toHaveBeenCalledTimes(reads);
  expect(reload).toHaveBeenCalledTimes(2);
});

test("does nothing for an ended run or without a run id", async () => {
  const onProgress = vi.fn();
  const reload = vi.fn();
  renderHook(() => useRunStream("run-1", false, onProgress, reload));
  renderHook(() => useRunStream(null, true, onProgress, reload));
  await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS * 2);
  expect(apiMock).not.toHaveBeenCalled();
  expect(reload).not.toHaveBeenCalled();
});

test("the next read waits for the last one: a slow server is not asked again while it has not answered", async () => {
  let answer: (value: unknown) => void = () => undefined;
  apiMock.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
  const reload = vi.fn(() => new Promise<void>(() => undefined)); // never answers
  renderHook(() => useRunStream("run-1", true, vi.fn(), reload));
  await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS * 6);
  expect(apiMock).toHaveBeenCalledTimes(1);
  expect(reload).toHaveBeenCalledTimes(1);
  answer({ stages_done: 1, stages_total: 8, finished: false });
  await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS);
  expect(apiMock).toHaveBeenCalledTimes(2);
});

test("a failed read backs off up to 30 seconds, and a success starts again from five", async () => {
  expect([1, 2, 3, 4, 5, 9].map((n) => backoff(POLL_PROGRESS_MS, n))).toEqual([5000, 10000, 20000, POLL_MAX_MS, POLL_MAX_MS, POLL_MAX_MS]);
  apiMock.mockRejectedValue(new ApiError(503, "unavailable", ""));
  renderHook(() => useRunStream("run-1", true, vi.fn(), vi.fn()));
  await vi.advanceTimersByTimeAsync(0);
  expect(apiMock).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS); // second read, 5 s after the first
  expect(apiMock).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS); // the next wait is 10 s
  expect(apiMock).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS);
  expect(apiMock).toHaveBeenCalledTimes(3);
});

test("a run that is gone (404) or a session that ended (401) is not asked about again", async () => {
  for (const status of [404, 401]) {
    apiMock.mockReset();
    apiMock.mockRejectedValue(new ApiError(status, "x", ""));
    const reload = vi.fn();
    const { unmount } = renderHook(() => useRunStream("run-1", true, vi.fn(), reload));
    await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS * 10);
    expect(apiMock).toHaveBeenCalledTimes(1);
    expect(reload).not.toHaveBeenCalled();
    unmount();
  }
  // the run record's own 404 stops the progress reads too
  apiMock.mockReset();
  apiMock.mockResolvedValue({ stages_done: 1, stages_total: 8, finished: false });
  const reload = vi.fn().mockRejectedValue(new ApiError(404, "not_found", ""));
  renderHook(() => useRunStream("run-1", true, vi.fn(), reload));
  await vi.advanceTimersByTimeAsync(POLL_RUN_MS + 1);
  const reads = apiMock.mock.calls.length;
  await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS * 10);
  expect(reload).toHaveBeenCalledTimes(1);
  expect(apiMock.mock.calls.length).toBe(reads);
});

test("a hidden tab reads nothing; coming back reads the run record at once and carries on", async () => {
  const reload = vi.fn();
  renderHook(() => useRunStream("run-1", true, vi.fn(), reload));
  await vi.advanceTimersByTimeAsync(0);
  expect(apiMock).toHaveBeenCalledTimes(1);
  await act(async () => { show("hidden"); });
  const reads = apiMock.mock.calls.length;
  await vi.advanceTimersByTimeAsync(POLL_RUN_MS * 6);
  expect(apiMock.mock.calls.length).toBe(reads);
  expect(reload).not.toHaveBeenCalled();
  await act(async () => { show("visible"); await vi.advanceTimersByTimeAsync(0); });
  expect(reload).toHaveBeenCalledTimes(1);
  expect(apiMock.mock.calls.length).toBe(reads + 1);
  await vi.advanceTimersByTimeAsync(POLL_RUN_MS);
  expect(reload).toHaveBeenCalledTimes(2);
});
