// New research from a company name, the run screen with its eight steps, and the Files tab.
import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

const DOMAIN = "run-e2e.example";

test("company name research opens the run screen and the account's Files tab", async ({ page }) => {
  await signIn(page, "acme-a", "user-a", "seller");
  await page.goto("/research/new");
  await expect(page.getByRole("heading", { name: "New research" })).toBeVisible();
  await page.getByLabel("Company name").fill("Run E2E");
  await page.getByLabel("Website (optional)").fill(DOMAIN);
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page).toHaveURL(/\/runs\//);
  await expect(page.getByRole("heading", { name: "Research run" })).toBeVisible();
  const steps = page.getByRole("list", { name: "Steps" }).getByRole("listitem");
  await expect(steps).toHaveCount(8);
  await expect(steps.first()).toContainText("Set up");
  await expect(page.getByRole("button", { name: "Stop and keep results" })).toBeVisible();

  await page.goto(`/accounts/${DOMAIN}/files`);
  await expect(page.getByRole("button", { name: "Download all" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Build files" })).toBeVisible();
});
