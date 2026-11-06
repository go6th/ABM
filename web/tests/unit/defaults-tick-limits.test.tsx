// @vitest-environment jsdom
// The operator Defaults page edits the scheduled-refresh tick's ceilings at runtime (ADR-0108,
// "dynamic" follow-up): the values load from the API, the per-client field never offers more than
// the tick's own number, and Save sends both.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const request = vi.fn();
const defaults = {
  account_cap_per_user: 34, idle_expiry_days: 7, monthly_credit_pool: 2000, fastest_schedule: "3d",
  credit_cap_per_pull: 350, models: [{ id: "claude-opus-5-5", name: "Claude Opus 5.5", enabled: true, needs_client_permission: false }],
  default_research_model: "claude-opus-5-5", default_analysis_model: "claude-opus-5-5",
  research_limit_default: 50, chat_limit_default: 500, limit_reset: "monthly", max_file_bytes: 20 * 1024 ** 3,
  run_dollar_cap_usd: 25, repeat_call_limit: 3, stall_after_s: 2400, stall_after_s_default: 1800,
  schedule_tick_max_starts: 10, schedule_tick_max_per_tenant: 3,
  skill_version: "1.11", skill_installed: true, admins: [], updated_by: "seed", updated_at: "2026-11-03T00:00:00Z",
};

vi.mock("@/lib/operator-api", () => ({ operatorRequest: (...args: unknown[]) => request(...args) }));
vi.mock("@/app/operator/(console)/useOperatorApi", () => ({
  useOperatorApi: () => ({ data: defaults, error: null, loading: false, reload: () => undefined }),
}));

import Defaults from "@/app/operator/(console)/defaults/page";

afterEach(() => { cleanup(); request.mockReset(); });

describe("schedule tick limits on the Defaults page", () => {
  it("shows the live values and saves the edited ones", async () => {
    request.mockResolvedValue(defaults);
    render(<Defaults />);
    const starts = screen.getByLabelText("Scheduled refreshes started at once") as HTMLInputElement;
    const perClient = screen.getByLabelText("Per client per tick") as HTMLInputElement;
    expect(starts.value).toBe("10");
    expect(perClient.value).toBe("3");
    fireEvent.change(starts, { target: { value: "20" } });
    fireEvent.change(perClient, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await vi.waitFor(() => expect(request).toHaveBeenCalled());
    const [key, options] = request.mock.calls[0] as [string, { body: Record<string, unknown> }];
    expect(key).toBe("PUT /operator/v1/console/defaults");
    expect(options.body).toMatchObject({ schedule_tick_max_starts: 20, schedule_tick_max_per_tenant: 5 });
  });

  it("shows the stall time with the platform's default beside it (ADR-0132)", () => {
    render(<Defaults />);
    expect((screen.getByLabelText("Stalled after") as HTMLInputElement).value).toBe("40");
    expect(screen.getByText("minutes (default 30 minutes)")).toBeTruthy();
  });

  it("never lets the per-client number pass the tick's own", () => {
    render(<Defaults />);
    const perClient = screen.getByLabelText("Per client per tick") as HTMLInputElement;
    fireEvent.change(perClient, { target: { value: "50" } });
    expect(perClient.value).toBe("10");
  });
});
