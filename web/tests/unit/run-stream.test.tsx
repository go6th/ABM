// @vitest-environment jsdom
// Following an open run without an event stream (jsdom has none): the progress is read at once
// and again every POLL_PROGRESS_MS, the run record is re-read every POLL_RUN_MS, and nothing
// runs for an ended run or after unmount.
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));

import { POLL_PROGRESS_MS, POLL_RUN_MS, useRunStream } from "@/lib/useRunStream";

beforeEach(() => { vi.useFakeTimers(); apiMock.mockReset(); apiMock.mockResolvedValue({ stages_done: 1, stages_total: 8, finished: false }); });
afterEach(() => { vi.useRealTimers(); });

test("polls progress and the run record while the run is open, and stops on unmount", async () => {
  const onProgress = vi.fn();
  const reload = vi.fn();
  const { unmount } = renderHook(() => useRunStream("run-1", true, onProgress, reload));
  await vi.advanceTimersByTimeAsync(0);
  expect(apiMock).toHaveBeenCalledWith("GET", "/v1/runs/run-1/progress");
  expect(onProgress).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS);
  expect(onProgress).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(POLL_RUN_MS * 2 - POLL_PROGRESS_MS);
  expect(reload).toHaveBeenCalledTimes(2);
  unmount();
  await vi.advanceTimersByTimeAsync(POLL_PROGRESS_MS * 3);
  expect(onProgress).toHaveBeenCalledTimes(2);
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
