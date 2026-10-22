// @vitest-environment jsdom
// The sign-in screens go through apiPublic: no session read and no CSRF token, the generated
// contract on what they send, and the same plain words for each kind of refusal.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

import LoginPage from "@/app/(auth)/login/page";
import ForgotPasswordPage from "@/app/(auth)/forgot/page";
import ResetPasswordPage from "@/app/(auth)/reset/page";
import AcceptInvitePage from "@/app/(auth)/invite/page";
import VerifyEmailPage from "@/app/(auth)/verify-email/page";

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };
let calls: Call[] = [];

function reply(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, method: init.method ?? "GET", headers: init.headers as Record<string, string>, body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  }));
}

beforeEach(() => { calls = []; replace.mockReset(); window.location.hash = ""; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("sign-in sends one contract-checked call with no CSRF token and no session read", async () => {
  reply(200, { signed_in: true, tenant_id: "t" });
  render(<LoginPage />);
  fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "ann@acme.example" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-long-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(["POST /v1/auth/login"]);
  expect(calls[0]!.headers).not.toHaveProperty("X-CSRF-Token");
});

test("sign-in offers the workspace choice when the email opens more than one", async () => {
  reply(409, { code: "tenant_selection_required", tenants: [{ tenant_id: "t1", role: "analyst" }, { tenant_id: "t2", role: "admin" }] });
  render(<LoginPage />);
  fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "ann@acme.example" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-long-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect((await screen.findByRole("status")).textContent).toContain("Your email opens more than one workspace.");
  const choice = screen.getByLabelText("Workspace") as HTMLSelectElement;
  expect([...choice.options].map((o) => o.value)).toEqual(["t1", "t2"]);
  fireEvent.change(choice, { target: { value: "t2" } });
  reply(200, { signed_in: true, tenant_id: "t2" });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  expect(calls.at(-1)!.body).toMatchObject({ workspace_id: "t2" });
});

test("forgot password confirms the request, and says when the service is down", async () => {
  reply(202, { accepted: true });
  render(<ForgotPasswordPage />);
  fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "ann@acme.example" } });
  fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
  expect((await screen.findByRole("status")).textContent).toContain("a reset link has been sent");
  expect(calls[0]).toMatchObject({ url: "/v1/auth/password/reset/request", body: { email: "ann@acme.example" } });
  cleanup();
  reply(503, { detail: "down" });
  render(<ForgotPasswordPage />);
  fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "ann@acme.example" } });
  fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
  expect((await screen.findByRole("alert")).textContent).toContain("temporarily unavailable");
});

async function choosePassword(label: string, button: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value: "a-long-password-12" } });
  fireEvent.change(screen.getByLabelText(new RegExp(`^Confirm`)), { target: { value: "a-long-password-12" } });
  await waitFor(() => expect((screen.getByRole("button", { name: button }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: button }));
}

test.each([
  [422, "Choose a password of at least 12 characters"],
  [400, "This reset link is invalid or expired"],
  [503, "temporarily unavailable"],
])("reset password explains a %s in plain words", async (status, text) => {
  window.location.hash = "#secret-token";
  reply(status, { detail: "x" });
  render(<ResetPasswordPage />);
  await choosePassword("New password", "Update password");
  expect((await screen.findByRole("alert")).textContent).toContain(text);
  expect(calls[0]).toMatchObject({ url: "/v1/auth/password/reset", body: { token: "secret-token", password: "a-long-password-12" } });
});

test("accepting an invite sends the token and password and reports success", async () => {
  window.location.hash = "#invite-token";
  reply(200, { password_updated: true });
  render(<AcceptInvitePage />);
  await choosePassword("Password", "Join");
  expect((await screen.findByRole("status")).textContent).toContain("You have joined.");
  expect(calls[0]).toMatchObject({ url: "/v1/auth/invite/accept", body: { token: "invite-token", password: "a-long-password-12" } });
});

test.each([
  [200, { verified: true }, "Your email is verified."],
  [400, { detail: "x" }, "invalid or expired"],
  [503, { detail: "x" }, "temporarily unavailable"],
])("email verification with a %s reply", async (status, body, text) => {
  window.location.hash = "#verify-token";
  reply(status, body);
  render(<VerifyEmailPage />);
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain(text));
  expect(calls[0]).toMatchObject({ url: "/v1/auth/email/verify", body: { token: "verify-token" } });
});
