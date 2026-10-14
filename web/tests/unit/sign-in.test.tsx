// @vitest-environment jsdom
// The workspace sign-in (PDF page 1) and the admin sign-in: what they send and what they say.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

import LoginPage from "@/app/(auth)/login/page";
import OperatorLoginPage from "@/app/operator/(auth)/login/page";

type Call = { url: string; body: Record<string, unknown> };
let calls: Call[] = [];

function reply(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, body: JSON.parse(String(init.body)) });
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  }));
}

function fill(email: string, password = "") {
  fireEvent.change(screen.getByLabelText("Work email"), { target: { value: email } });
  if (password) fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
}

beforeEach(() => { calls = []; replace.mockReset(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

test("the page shows the design's copy and keeps me signed in by default", () => {
  render(<LoginPage />);
  expect(screen.getByRole("heading", { level: 1, name: "Sign in" })).toBeTruthy();
  expect(screen.getByText("6th is a go-to-market consultancy.")).toBeTruthy();
  expect((screen.getByLabelText("Keep me signed in on this device") as HTMLInputElement).checked).toBe(true);
  expect(screen.getByRole("link", { name: "Forgot password?" }).getAttribute("href")).toBe("/forgot");
  expect(screen.getByRole("link", { name: "Sign in to the admin console" }).getAttribute("href")).toBe("/operator/login");
  for (const grade of ["Confirmed", "Probable", "Hypothesis", "Lead"]) expect(screen.getByText(grade)).toBeTruthy();
});

test("Show and Hide reveal the password", () => {
  render(<LoginPage />);
  const field = screen.getByLabelText("Password") as HTMLInputElement;
  expect(field.type).toBe("password");
  fireEvent.click(screen.getByRole("button", { name: "Show password" }));
  expect(field.type).toBe("text");
  expect(screen.getByRole("button", { name: "Hide password" }).textContent).toBe("Hide");
});

test.each([[true], [false]])("Keep me signed in %s is sent with the sign-in", async (keep) => {
  reply(200, { signed_in: true, tenant_id: "t" });
  render(<LoginPage />);
  fill("ann@acme.example", "a-long-password");
  if (!keep) fireEvent.click(screen.getByLabelText("Keep me signed in on this device"));
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  expect(calls[0]).toEqual({ url: "/v1/auth/login", body: { email: "ann@acme.example", password: "a-long-password", remember: keep } });
});

test.each([
  [401, "That email and password don't match."],
  [429, "Too many attempts, try again in a minute."],
])("a %s sign-in says so in plain words", async (status, text) => {
  reply(status, { detail: "x" });
  render(<LoginPage />);
  fill("ann@acme.example", "wrong");
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect((await screen.findByRole("alert")).textContent).toContain(text);
  expect(replace).not.toHaveBeenCalled();
});

test("Continue with SSO asks for the email first", () => {
  reply(200, {});
  render(<LoginPage />);
  fireEvent.click(screen.getByRole("button", { name: /Continue with SSO/ }));
  expect(screen.getByText("Enter your work email first, then choose Continue with SSO.")).toBeTruthy();
  expect(calls).toEqual([]);
});

test("Continue with SSO goes to the company's sign-in when one is found", async () => {
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, assign });
  reply(200, { next: "sso", start_url: "/v1/sso/acme/start?return_to=%2F" });
  render(<LoginPage />);
  fill("ann@acme.example");
  fireEvent.click(screen.getByRole("button", { name: /Continue with SSO/ }));
  await waitFor(() => expect(assign).toHaveBeenCalledWith("/v1/sso/acme/start?return_to=%2F"));
  expect(calls[0]).toEqual({ url: "/v1/sso/discover", body: { email: "ann@acme.example" } });
});

test("Continue with SSO carries Keep me signed in off to the company's sign-in", async () => {
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, assign });
  reply(200, { next: "sso", start_url: "/v1/sso/acme/start?return_to=%2F" });
  render(<LoginPage />);
  fill("ann@acme.example");
  fireEvent.click(screen.getByLabelText("Keep me signed in on this device"));
  fireEvent.click(screen.getByRole("button", { name: /Continue with SSO/ }));
  await waitFor(() => expect(assign).toHaveBeenCalledWith("/v1/sso/acme/start?return_to=%2F&remember=false"));
});

test("Continue with SSO says to use the password when no company sign-in is found", async () => {
  reply(200, { next: "password", start_url: null });
  render(<LoginPage />);
  fill("ann@acme.example");
  fireEvent.click(screen.getByRole("button", { name: /Continue with SSO/ }));
  expect((await screen.findByRole("status")).textContent).toContain("Your company signs in with a password here.");
});

const STAFF = {
  OPERATOR_GOOGLE_CLIENT_ID: "client",
  OPERATOR_GOOGLE_CLIENT_SECRET: "secret",
  OPERATOR_GOOGLE_REDIRECT_URI: "http://localhost:3000/api/operator/callback",
  STAFF_WORKSPACE_DOMAIN: "6th.example",
};

test("the admin page says when staff sign-in is not set up, with no broken button", async () => {
  for (const name of Object.keys(STAFF)) vi.stubEnv(name, "");
  render(await OperatorLoginPage({ searchParams: Promise.resolve({}) }));
  expect(screen.getByRole("heading", { level: 1, name: "Admin sign in" })).toBeTruthy();
  expect(screen.getByRole("status").textContent).toBe("Staff sign-in is not set up on this server.");
  expect(screen.queryByRole("link", { name: /Sign in with Google/ })).toBeNull();
  expect(screen.getByRole("link", { name: "Go to the workspace sign in" }).getAttribute("href")).toBe("/login");
});

test("the admin page starts Google sign-in when set up, and explains a refusal", async () => {
  for (const [name, value] of Object.entries(STAFF)) vi.stubEnv(name, value);
  render(await OperatorLoginPage({ searchParams: Promise.resolve({ error: "staff_access_refused" }) }));
  expect(screen.getByRole("link", { name: /Sign in with Google/ }).getAttribute("href")).toBe("/api/operator/login?return_to=%2Foperator");
  expect(screen.getByRole("alert").textContent).toContain("This Google account is not on the 6th staff list.");
});
