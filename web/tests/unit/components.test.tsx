// @vitest-environment jsdom
import { cleanup, render, screen, act } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { RunProgress } from "@/components/Progress";
import { Button, DataTable, GradeBadge, Input, Progress, StatusChip } from "@/design/components";
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

test("buttons expose disabled loading state and input validation is associated with its label", () => {
  render(
    <>
      <Button loading>Save account</Button>
      <Input label="Company domain" required hint="Use the primary domain." error="This domain is already in use." />
    </>,
  );

  expect(screen.getByRole("button", { name: "Save account" }).hasAttribute("disabled")).toBe(true);
  const input = screen.getByRole("textbox", { name: "Company domain" });
  expect(input.getAttribute("aria-invalid")).toBe("true");
  expect((input as HTMLInputElement).required).toBe(true);
  const describedBy = input.getAttribute("aria-describedby") ?? "";
  expect(describedBy).toContain(screen.getByText("Use the primary domain.").id);
  expect(describedBy).toContain(screen.getByText("This domain is already in use.").id);
});

test("evidence and coverage badges use explicit human-readable labels", () => {
  render(
    <>
      <GradeBadge grade="hypothesis" />
      <StatusChip status="partial" />
    </>,
  );

  expect(screen.getByLabelText("Evidence grade: Hypothesis")).toBeTruthy();
  expect(screen.getByLabelText("Coverage status: Partial")).toBeTruthy();
});

test("progress remains indeterminate when the backend has no valid measured value", () => {
  const { rerender } = render(<Progress label="Research progress" />);
  expect(screen.getByRole("progressbar", { name: "Research progress" }).hasAttribute("value")).toBe(false);

  rerender(<Progress label="Research progress" value={2} max={4} />);
  expect(screen.getByRole("progressbar", { name: "Research progress" }).getAttribute("value")).toBe("2");

  rerender(<Progress label="Research progress" value={5} max={4} />);
  expect(screen.getByRole("progressbar", { name: "Research progress" }).hasAttribute("value")).toBe(false);
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
  expect(screen.getByRole("status").textContent).toBe("Loading records…");

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
