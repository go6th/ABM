// Tenant isolation and the client screens: a member of workspace A sees only A's accounts, the
// Ask screen answers from the workspace, the sidebar lists the live screens, and the screens
// removed from the product are gone (their redirect stubs were deleted, fix prompt B task 5).
// Screens as redesigned on 2026-11-07: the sidebar is Ask, Accounts, Research, Settings.
import { expect, test } from "@playwright/test";
import { noSeriousA11y, seededSlug, signIn } from "./helpers";

test("a member sees only their own workspace's accounts", async ({ page }) => {
  await signIn(page, await seededSlug(page, "a"), "sam", "seller");
  await page.goto("/accounts");
  await expect(page.getByRole("heading", { name: "Accounts", exact: true })).toBeVisible();
  await expect(page.getByText("acme-a.example").first()).toBeVisible();
  await expect(page.getByText("beta-only.example")).toHaveCount(0);
  await noSeriousA11y(page);
});

test("the account page shows its tabs and the research state", async ({ page }) => {
  await signIn(page, await seededSlug(page, "a"), "sam", "seller");
  await page.goto("/accounts/acme-a.example");
  await expect(page.getByRole("heading", { name: "acme-a.example", level: 1 })).toBeVisible();
  for (const tab of ["Overview", "People", "Jobs", "Technology", "Signals", "News", "Sources", "Files"]) {
    await expect(page.getByRole("navigation", { name: "acme-a.example sections" }).getByRole("link", { name: tab, exact: true })).toBeVisible();
  }
  await expect(page.getByRole("button", { name: "Refresh", exact: true })).toBeVisible();
  await noSeriousA11y(page);
});

test("ask answers from the workspace and keeps the thread in Recent", async ({ page }) => {
  await signIn(page, await seededSlug(page, "a"), "sam", "seller");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What do you want to know?" })).toBeVisible();
  // the composer is the question, Accounts and the model (its effort inside); no "+" or Search
  await expect(page.getByRole("button", { name: /^Search/ })).toHaveCount(0);
  await page.getByLabel("Ask a question").fill("Summarize our account notes");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  // the hermetic model gives the workspace answer (no provider is ever called)
  await expect(page.getByText(/^From your workspace\./)).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".bubble").getByText("Summarize our account notes", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "New chat" }).click();
  await expect(page.getByRole("heading", { name: "What do you want to know?" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Recent chats" }).getByRole("link", { name: "Summarize our account notes" })).toBeVisible();
});

test("removed screens are gone", async ({ page }) => {
  await signIn(page, await seededSlug(page, "a"), "sam", "seller");
  for (const path of ["/alerts", "/audit", "/usage", "/settings/connectors", "/downloads", "/chat", "/runs"]) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBe(404);
  }
});

test("the sidebar lists the live screens a seller can open", async ({ page }) => {
  await signIn(page, await seededSlug(page, "a"), "sam", "seller");
  await page.goto("/accounts");
  const nav = page.getByRole("navigation", { name: "Main" });
  for (const name of ["Ask", "Accounts", "Research", "Settings"]) {
    await expect(nav.getByRole("link", { name, exact: true })).toBeVisible();
  }
  // admin-only settings are not offered to a seller
  await page.goto("/settings");
  const tabs = page.getByRole("navigation", { name: "Settings", exact: true });
  for (const name of ["Models", "Auto refresh"]) {
    await expect(tabs.getByRole("link", { name, exact: true })).toBeVisible();
  }
  for (const name of ["Team", "Support access"]) {
    await expect(tabs.getByRole("link", { name, exact: true })).toHaveCount(0);
  }
});

test("the sidebar shows no account cap or usage limit", async ({ page }) => {
  await signIn(page, await seededSlug(page, "a"), "sam", "seller");
  await page.goto("/accounts");
  await expect(page.getByText(/of \d+ accounts/)).toHaveCount(0);
  await expect(page.getByText(/credit/i)).toHaveCount(0);
});
