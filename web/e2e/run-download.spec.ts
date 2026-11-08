// New research from a company name: the two-press start, the account page's live card with Stop,
// the Research board's detail of the queued research, and the account's Files tab. (The harness
// drives runs only on request, so this research stays queued.)
import { expect, test } from "@playwright/test";
import { seededSlug, signIn } from "./helpers";

const DOMAIN = "run-e2e.example";

test("company name research shows its steps on the account and the Files tab", async ({ page }) => {
  await signIn(page, await seededSlug(page, "a"), "sam", "seller");
  await page.goto("/research");
  await expect(page.getByRole("heading", { name: "Research", exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Company name" }).fill("Run E2E");
  await page.getByRole("textbox", { name: "Website" }).fill(DOMAIN);
  // the first press only arms the start; the second one starts it
  await page.getByRole("button", { name: /^Start research/ }).click();
  await expect(page.getByText("Are you sure?")).toBeVisible();
  await page.getByRole("button", { name: /^Yes, start/ }).click();
  await expect(page).toHaveURL(new RegExp(`/accounts/${DOMAIN.replace(".", "\\.")}$`));
  const live = page.getByRole("region", { name: /^Researching from the web/ });
  await expect(live.getByRole("button", { name: "Stop research" })).toBeVisible();
  await live.getByRole("link", { name: "Watch it live" }).click();
  await expect(page).toHaveURL(/\/research\?r=/);
  const detail = page.getByRole("region", { name: "Run E2E" });
  await expect(detail.getByRole("heading", { name: "Steps" })).toBeVisible();
  await expect(detail.getByRole("link", { name: "Open account" })).toBeVisible();

  await page.goto(`/accounts/${DOMAIN}/files`);
  await expect(page.getByRole("button", { name: "Download everything" })).toBeVisible();
});
