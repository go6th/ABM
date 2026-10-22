// @vitest-environment jsdom
// Server pages whose optional reads are independent read them together: the workspace shell's
// recent threads and notifications, and support access's grants and events.
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Me } from "@/lib/gen/api.gen";

const serverGet = vi.fn();
vi.mock("@/lib/server-api", async (original) => ({
  ...(await original<typeof import("@/lib/server-api")>()),
  serverGet: (...args: unknown[]) => serverGet(...args),
}));
vi.mock("next/navigation", () => ({ redirect: (to: string) => { throw new Error(`redirect:${to}`); } }));
vi.mock("@/components/ws/WorkspaceShell", () => ({
  WorkspaceShell: (p: { recent: { id: string }[]; notes: { id: string }[]; children: React.ReactNode }) => (
    <div data-testid="shell" data-recent={p.recent.map((r) => r.id).join(",")} data-notes={p.notes.map((n) => n.id).join(",")}>{p.children}</div>
  ),
}));
vi.mock("@/components/accounts/SupportAccess", () => ({
  SupportAccess: (p: { grants: unknown[]; events: unknown[] }) => <div data-testid="support" data-grants={p.grants.length} data-events={p.events.length} />,
}));

import { ServerApiError } from "@/lib/server-api";
import ClientLayout from "@/app/(client)/layout";
import SupportAccessPage from "@/app/(client)/support-access/page";

const ME: Me = {
  capabilities: [], display_name: "Ann", email: "ann@acme.example", feature_flags: {}, isolation_tier: "shared",
  mfa_enabled: false, mfa_required: false, mfa_step_up_fresh: false, plan: "team", plan_limits: {}, role: "analyst",
  tenant_id: "t", tenant_name: "Acme", user_id: "u",
};

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

// the mock is cleared inside each test: clearing it in a beforeEach hook makes Vitest 5 report
// the page's own rejected reads as unhandled
afterEach(cleanup);

test("the shell reads threads and notifications together, and one failing leaves the other in place", async () => {
  serverGet.mockClear();
  const threads = deferred<unknown>();
  const notifications = deferred<unknown>();
  serverGet.mockImplementation((path: string) => {
    if (path === "/v1/me") return Promise.resolve(ME);
    if (path === "/v1/chat/threads") return threads.promise;
    return notifications.promise;
  });
  const rendering = ClientLayout({ children: <span>page</span> });
  await Promise.resolve();
  await Promise.resolve();
  // both optional reads are in flight before either has answered
  expect(serverGet.mock.calls.map((c) => c[0])).toEqual(["/v1/me", "/v1/chat/threads", "/v1/notifications"]);
  threads.reject(new ServerApiError(503, "ref"));
  notifications.resolve({ unread: 1, notifications: [{ id: "n1", kind: "research_finished", read: false, created_at: "2026-10-22T00:00:00Z", account_domain: "acme.example", account_name: "Acme", credits_used: null, reason: null, scope: null }] });
  const { getByTestId } = render(await rendering);
  expect(getByTestId("shell").getAttribute("data-recent")).toBe("");
  expect(getByTestId("shell").getAttribute("data-notes")).toBe("n1");
  expect(getByTestId("shell").textContent).toBe("page");
});

test("support access reads grants and events together; a 403 on either explains who may grant", async () => {
  serverGet.mockClear();
  serverGet.mockImplementation((path: string) =>
    path === "/v1/support/grants" ? Promise.reject(new ServerApiError(403, "ref")) : Promise.resolve({ events: [] }));
  const { container } = render(await SupportAccessPage());
  expect(serverGet).toHaveBeenCalledTimes(2);
  expect(container.textContent).toContain("Only your administrator can give support access");

  serverGet.mockImplementation((path: string) =>
    Promise.resolve(path === "/v1/support/grants" ? { grants: [{ id: "g" }] } : { events: [] }));
  const ok = render(await SupportAccessPage());
  expect(ok.getByTestId("support").getAttribute("data-grants")).toBe("1");
});

test("support access still throws an error that is not the API's", async () => {
  serverGet.mockClear();
  serverGet.mockImplementation(() => Promise.reject(new Error("boom")));
  const outcome = await SupportAccessPage().then(() => "rendered", (e: unknown) => (e instanceof Error ? e.message : "other"));
  expect(outcome).toBe("boom");
});
