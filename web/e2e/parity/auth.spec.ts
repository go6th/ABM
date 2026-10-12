// Sign in and the other auth pages: behaviour the rebuild must keep.
import { test, expect } from "@playwright/test";
import { seeded, signIn } from "./support";

test("password sign-in refuses an unknown user and a wrong password with the same message", async ({ page }) => {
  await page.goto("/login");
  const tryWith = async (email: string) => {
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill("not-the-password-123");
    await page.getByRole("button", { name: /^sign in/i }).click();
    const alert = page.getByRole("alert").filter({ hasText: /\S/ });
    await expect(alert.first()).toBeVisible();
    return (await alert.first().innerText()).trim();
  };
  const unknown = await tryWith("nobody-here@synthetic.test");
  const wrong = await tryWith("ada@synthetic.test");
  expect(unknown).toBe(wrong);
  await expect(page).toHaveURL(/\/login/);
});

test("the password field can be revealed and hidden", async ({ page }) => {
  await page.goto("/login");
  const field = page.getByLabel("Password", { exact: true });
  await field.fill("secret-value");
  await expect(field).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Show password" }).click();
  await expect(field).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "Hide password" }).click();
  await expect(field).toHaveAttribute("type", "password");
});

test("enterprise SSO signs a member in and lands in the workspace", async ({ page, request }) => {
  const s = await seeded(request);
  await signIn(page, s.slug_a, "ada", "admin");
  await expect(page.getByRole("navigation", { name: "Workspace" })).toBeVisible();
});

test("an invalid SSO sign-in ID is refused before leaving the page", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("tab", { name: "Enterprise SSO" }).click();
  await page.getByLabel("Organization sign-in ID").fill("X");
  await page.getByRole("button", { name: "Continue with SSO" }).click();
  await expect(page.getByRole("alert").filter({ hasText: /sign-in ID/i })).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

test("forgot password confirms without revealing whether the account exists", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("link", { name: /forgot password/i }).click();
  await expect(page).toHaveURL(/\/forgot/);
  await page.getByLabel("Work email").fill("someone@synthetic.test");
  await page.getByRole("button", { name: /send reset link/i }).click();
  await expect(page.getByRole("status").filter({ hasText: /if that address has an active account/i })).toBeVisible();
});

test("a reset link without its token says it is incomplete", async ({ page }) => {
  await page.goto("/reset");
  await page.getByLabel("New password", { exact: true }).fill("a-long-enough-password");
  await page.getByLabel("Confirm new password").fill("a-long-enough-password");
  await expect(page.getByRole("button", { name: /update password/i })).toBeDisabled();
});

test("a verification link without its token says it is incomplete", async ({ page }) => {
  await page.goto("/verify-email");
  await expect(page.getByRole("status").filter({ hasText: /incomplete or expired/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /sign in/i })).toBeVisible();
});

test("signed-out visitors are sent to sign in", async ({ page }) => {
  await page.goto("/accounts");
  await expect(page).toHaveURL(/\/login/);
});

test("pages carry a nonce CSP and the security headers", async ({ page }) => {
  const res = await page.goto("/login");
  const h = res!.headers();
  expect(h["content-security-policy"]).toMatch(/script-src[^;]*'nonce-/);
  expect(h["x-frame-options"]).toBe("DENY");
  expect(h["x-content-type-options"]).toBe("nosniff");
});
