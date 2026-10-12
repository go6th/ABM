// @vitest-environment jsdom
import { cleanup, render, screen, act } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { RunProgress } from "@/ui/RunProgress";
import { DataTable } from "@/ui/DataTable";
import { Input } from "@/ui/Input";
import { Meter } from "@/ui/primitives";
import { GradeMark } from "@/components/ws/GradeMark";
import { useApi } from "@/lib/useApi";

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

function ApiProbe({ path }: { path: string }) {
  const { data } = useApi<{ runs: unknown[] }>(path);
  return <p role="status">{data ? `Runs: ${data.runs.length}` : "Loading records"}</p>;
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

test("input validation is associated with its label", () => {
  render(<Input label="Company domain" required hint="Use the primary domain." error="This domain is already in use." />);

  const input = screen.getByRole("textbox", { name: "Company domain" });
  expect(input.getAttribute("aria-invalid")).toBe("true");
  expect((input as HTMLInputElement).required).toBe(true);
  const describedBy = input.getAttribute("aria-describedby") ?? "";
  expect(describedBy).toContain(screen.getByText("Use the primary domain.").id);
  expect(describedBy).toContain(screen.getByText("This domain is already in use.").id);
});

test("a grade mark carries its plain label, shown or as text for assistive technology", () => {
  const { rerender } = render(<GradeMark grade="hypothesis" />);
  expect(screen.getByText("Hypothesis")).toBeTruthy();
  rerender(<GradeMark grade="probable" showLabel={false} />);
  expect(screen.getByText("Probable").className).toBe("sr-only");
});

test("a meter is labelled and its fill never leaves the track", () => {
  const { container, rerender } = render(<Meter value={2} max={4} label="Half used" />);
  expect(screen.getByRole("img", { name: "Half used" })).toBeTruthy();
  expect(container.querySelector(".fill")?.getAttribute("width")).toBe("50");
  rerender(<Meter value={9} max={4} label="Over" />);
  expect(container.querySelector(".fill")?.getAttribute("width")).toBe("100");
  rerender(<Meter value={0} max={0} />);
  expect(container.querySelector(".fill")?.getAttribute("width")).toBe("0");
});

test("data table renders rows and communicates empty, loading, and error states", () => {
  const columns = [{ key: "name", header: "Account", render: (row: { name: string }) => row.name }];
  const { rerender } = render(
    <DataTable caption="Accounts" columns={columns} rows={[{ name: "Example Corp" }]} rowKey={(row) => row.name} />,
  );
  expect(screen.getByText("Example Corp")).toBeTruthy();

  rerender(<DataTable caption="Accounts" columns={columns} rows={[]} rowKey={(row) => row.name} />);
  expect(screen.getByText("No records to show.")).toBeTruthy();

  rerender(<DataTable caption="Accounts" columns={columns} rows={[]} loading rowKey={(row) => row.name} />);
  expect(screen.getByRole("status").textContent).toBe("Loading records");

  rerender(<DataTable caption="Accounts" columns={columns} rows={[]} error="Accounts are unavailable." rowKey={(row) => row.name} />);
  expect(screen.getByRole("alert").textContent).toBe("Accounts are unavailable.");
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
