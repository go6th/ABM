// Tenant isolation and the client screens: a member of workspace A sees only A's accounts, the
// Ask screen answers from the workspace, and the removed screens redirect home.
import { expect, test } from "@playwright/test";
import { noSeriousA11y, signIn } from "./helpers";

test("a member sees only their own workspace's accounts", async ({ page }) => {
  await signIn(page, "acme-a", "user-a", "seller");
  await page.goto("/accounts");
  await expect(page.getByRole("heading", { name: "Accounts" })).toBeVisible();
  await expect(page.getByText("acme-a.example").first()).toBeVisible();
  await expect(page.getByText("beta-only.example")).toHaveCount(0);
  await noSeriousA11y(page);
});

test("the account page shows its tabs and the research state", async ({ page }) => {
  await signIn(page, "acme-a", "user-a", "seller");
  await page.goto("/accounts/acme-a.example");
  await expect(page.getByRole("heading", { name: "acme-a.example" })).toBeVisible();
  for (const tab of ["Overview", "People", "Jobs", "Technology", "News", "Sources", "Files"]) {
    await expect(page.getByRole("navigation", { name: "Account sections" }).getByRole("link", { name: tab })).toBeVisible();
  }
  await expect(page.getByRole("button", { name: "Refresh from web" })).toBeVisible();
  await noSeriousA11y(page);
});

test("ask answers from the workspace and keeps the thread in Recent", async ({ page }) => {
  await signIn(page, "acme-a", "user-a", "seller");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Ask about your accounts" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Search mode" }).getByRole("button", { name: "Normal" })).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Ask a question").fill("Summarize our account notes");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Synthetic response")).toBeVisible();
  await expect(page.locator(".q").getByText("Summarize our account notes", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "New chat" }).click();
  await expect(page.getByRole("heading", { name: "Ask about your accounts" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Workspace" }).getByRole("link", { name: "Summarize our account notes" })).toBeVisible();
});

test("removed screens redirect to Ask", async ({ page }) => {
  await signIn(page, "acme-a", "user-a", "seller");
  for (const path of ["/alerts", "/audit", "/usage", "/settings", "/settings/connectors", "/downloads", "/chat"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/(\?.*)?$/);
  }
});

test("the sidebar shows no account cap or usage limit", async ({ page }) => {
  await signIn(page, "acme-a", "user-a", "seller");
  await page.goto("/accounts");
  await expect(page.getByText(/of \d+ accounts/)).toHaveCount(0);
  await expect(page.getByText(/credit/i)).toHaveCount(0);
});
