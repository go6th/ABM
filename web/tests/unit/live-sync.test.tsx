// @vitest-environment jsdom
// The account page re-draws itself as files are read, so an upload shows in the Deep dive counts,
// Files and Your uploaded files without a reload. SYNTHETIC data only.
import { cleanup, render } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
const api = vi.fn();
vi.mock("@/lib/api", () => ({ api: (...args: unknown[]) => api(...args) }));

import { AFTER_READY_MS, FOCUS_GAP_MS, LiveSync, POLL_GIVE_UP_MS, POLL_MS, RetryRefresh, docsSignature, pollEvery } from "@/components/accounts/LiveSync";

const doc = (id: string, stage: string, passages = 0, indexed = 0) => ({ document_id: id, stage, needs_company: false, passages, parts_done: 0, members_read: 0, indexed_passages: indexed }) as never;
const list = (...docs: unknown[]) => ({ documents: docs });

beforeEach(() => { vi.useFakeTimers(); refresh.mockReset(); api.mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const flush = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

test("the signature changes with a file's stage and passages, not with its order of arrival in time", () => {
  expect(docsSignature([doc("a", "parsing"), doc("b", "indexed", 5)])).toBe("a:parsing:0:0:0:0|b:indexed:5:0:0:0");
  // indexing progress moves while the stage and the total stay the same
  expect(docsSignature([doc("a", "indexing", 900, 100)])).not.toBe(docsSignature([doc("a", "indexing", 900, 200)]));
  expect(docsSignature([doc("a", "indexed", 9)])).not.toBe(docsSignature([doc("a", "indexed", 8)]));
});

test("while a file is read the page asks every few seconds and re-draws when its stage changes, then a few times after", async () => {
  api.mockResolvedValue(list(doc("a", "parsing")));
  const { rerender } = render(<LiveSync accountId="acct" docs={[doc("a", "parsing")]} />);
  await flush(); // the check on arrival: the same files as drawn
  expect(refresh).not.toHaveBeenCalled();
  await flush(POLL_MS);
  expect(refresh).not.toHaveBeenCalled(); // still parsing: nothing to show yet
  api.mockResolvedValue(list(doc("a", "indexed", 40)));
  await flush(POLL_MS);
  expect(refresh).toHaveBeenCalledTimes(1);
  // the page came back drawn from the finished file: no more polling, three re-draws follow
  rerender(<LiveSync accountId="acct" docs={[doc("a", "indexed", 40)]} />);
  const calls = api.mock.calls.length;
  await flush(AFTER_READY_MS[0]!);
  expect(refresh).toHaveBeenCalledTimes(2);
  await flush(AFTER_READY_MS[1]! - AFTER_READY_MS[0]!);
  expect(refresh).toHaveBeenCalledTimes(3);
  await flush(AFTER_READY_MS[2]! - AFTER_READY_MS[1]!);
  expect(refresh).toHaveBeenCalledTimes(4);
  await flush(120000);
  expect(refresh).toHaveBeenCalledTimes(4); // and then it stops
  expect(api.mock.calls.length).toBe(calls); // an idle page asks nothing more
});

test("a page restored from history older than the files is re-drawn on arrival", async () => {
  api.mockResolvedValue(list(doc("a", "indexed", 3), doc("new-zip", "indexed", 900)));
  render(<LiveSync accountId="acct" docs={[doc("a", "indexed", 3)]} />);
  await flush();
  expect(refresh).toHaveBeenCalledTimes(1);
});

test("coming back to the tab re-draws once, never more than every 15 seconds", async () => {
  api.mockResolvedValue(list());
  render(<LiveSync accountId="acct" docs={[]} />);
  await flush();
  const show = () => { Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true }); document.dispatchEvent(new Event("visibilitychange")); };
  show();
  expect(refresh).not.toHaveBeenCalled(); // drawn a moment ago
  await flush(FOCUS_GAP_MS + 1);
  show();
  expect(refresh).toHaveBeenCalledTimes(1);
  show();
  expect(refresh).toHaveBeenCalledTimes(1);
});

test("a failed check is tried again, never shown as an error", async () => {
  api.mockRejectedValueOnce(new Error("offline")).mockResolvedValue(list(doc("a", "indexed")));
  render(<LiveSync accountId="acct" docs={[doc("a", "scanning")]} />);
  await flush();
  expect(refresh).not.toHaveBeenCalled();
  await flush(POLL_MS);
  expect(refresh).toHaveBeenCalledTimes(1);
});

test("a stuck upload is polled less often and then given up on, not every four seconds for ever", async () => {
  expect(pollEvery(0)).toBe(POLL_MS);
  expect(pollEvery(61_000)).toBe(10_000);
  expect(pollEvery(301_000)).toBe(30_000);
  expect(pollEvery(POLL_GIVE_UP_MS)).toBeNull();
  api.mockResolvedValue(list(doc("a", "uploading")));
  render(<LiveSync accountId="acct" docs={[doc("a", "uploading")]} />);
  await flush(POLL_GIVE_UP_MS + 120_000);
  const calls = api.mock.calls.length;
  expect(calls).toBeLessThan(80); // about 15 + 24 + 20 checks, not 225
  await flush(10 * 60_000);
  expect(api.mock.calls.length).toBe(calls); // and it has stopped
  expect(refresh).not.toHaveBeenCalled(); // nothing changed, nothing was re-drawn
});

test("when the uploads could not be read the page tries once more after fifteen seconds", async () => {
  render(<RetryRefresh />);
  await flush(14_000);
  expect(refresh).not.toHaveBeenCalled();
  await flush(2_000);
  expect(refresh).toHaveBeenCalledTimes(1);
});
