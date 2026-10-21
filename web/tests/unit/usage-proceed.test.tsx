// @vitest-environment jsdom
// "This much will be used, should I proceed" (owner decision 2026-10-21): each refresh scope shows
// its expected tokens and dollars, Confirm asks first, and nothing is sent until Proceed.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { RefreshForm } from "@/components/accounts/RefreshForm";
import type { RefreshOptions } from "@/lib/gen/api.gen";
import { limitLine } from "@/lib/limits";
import { dollars, tokenCount, usageBasis, usageLine } from "@/lib/usage";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const pool = { period: "2026-10", pool: 2000, used: 0, held: 0, left: 2000, resets_on: "2026-11-01" };
const view = {
  domain: "acme.example",
  name: "Acme",
  refreshed_days_ago: 3,
  allowed: true,
  credits: pool,
  cap_per_pull: 400,
  options: [
    { scope: "jobs", name: "Job postings only", description: "Roles", minutes: 3, credits: 20, affordable: true, usage: { tokens: 300000, cost_usd: 1, runs: 0 } },
    { scope: "people", name: "People and teams", description: "People", minutes: 10, credits: 60, affordable: true, usage: { tokens: 612000, cost_usd: 2.5, runs: 3 } },
    { scope: "full", name: "Full refresh", description: "All", minutes: 45, credits: 220, affordable: true, usage: { tokens: 2500000, cost_usd: 11, runs: 0 } },
  ],
  topic: { minutes: 8, credits: 40, affordable: true, usage: { tokens: 400000, cost_usd: 2, runs: 0 } },
  open_job: null,
} as unknown as RefreshOptions;

test("usage reads in plain words", () => {
  expect(tokenCount(2_500_000)).toBe("2.5M");
  expect(tokenCount(612_000)).toBe("612K");
  expect(tokenCount(950)).toBe("950");
  expect(dollars(11)).toBe("$11.00");
  expect(dollars(0.004)).toBe("under $0.01");
  expect(usageLine({ tokens: 2_500_000, cost_usd: 11, runs: 0 })).toBe("About 2.5M tokens, about $11.00");
  expect(usageBasis({ tokens: 1, cost_usd: 1, runs: 0 })).toMatch(/rough estimate/i);
  expect(usageBasis({ tokens: 1, cost_usd: 1, runs: 3 })).toBe("Based on your last 3 finished runs of this kind.");
  const limits = { researches: { used: 2, limit: 1_000_000, left: 999_998 }, chats: { used: 0, limit: 40, left: 40 }, reset: "monthly", resets_on: null, pending_requests: [] };
  expect(limitLine(limits as never, "researches")).toBe("No research limit for now");
});

test("confirm shows the expected use and only Proceed starts the refresh", async () => {
  const posts: string[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") {
      posts.push(url);
      return Promise.resolve(new Response(JSON.stringify({ job_id: "j1", status: "queued", credits: 60 }), { status: 201 }));
    }
    if (url.includes("/v1/me/limits")) {
      return Promise.resolve(new Response(JSON.stringify({ researches: { used: 0, limit: 1_000_000, left: 1_000_000 }, chats: { used: 0, limit: 40, left: 40 }, reset: "monthly", resets_on: "2026-11-01", pending_requests: [] }), { status: 200 }));
    }
    if (url.includes("/v1/sso/session")) return Promise.resolve(new Response("{}", { status: 401 }));
    return Promise.resolve(new Response("{}", { status: 404 }));
  }));

  render(<RefreshForm view={view} initialTopic="" />);
  expect(screen.getByText("612K tokens, $2.50")).toBeTruthy(); // on the scope card
  fireEvent.click(screen.getByRole("button", { name: "Confirm refresh" }));
  const panel = screen.getByRole("group", { name: "Confirm the expected use" });
  expect(panel.textContent).toContain("This refresh will use about 612K tokens (about $2.50). Proceed?");
  expect(panel.textContent).toContain("Based on your last 3 finished runs of this kind.");
  expect(posts).toEqual([]); // nothing sent before Proceed

  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("group", { name: "Confirm the expected use" })).toBeNull();
  expect(posts).toEqual([]);

  fireEvent.click(screen.getByRole("radio", { name: /Full refresh/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm refresh" }));
  expect(screen.getByRole("group", { name: "Confirm the expected use" }).textContent).toContain("about 2.5M tokens (about $11.00)");
  fireEvent.click(screen.getByRole("button", { name: "Proceed" }));
  await waitFor(() => expect(posts).toEqual(["/v1/accounts/acme.example/refresh"]));
});
