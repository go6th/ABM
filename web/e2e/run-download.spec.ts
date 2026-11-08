// New research from a company name: the Research page's start pop-up with its two-press start, the
// research opened on the board, the account page's live card with Stop and its "Watch it live"
// link back to the board, and the account's Files tab. (The harness
// drives runs only on request, so this research stays queued.)
import { expect, test } from "@playwright/test";
import { seededSlug, signIn } from "./helpers";

const DOMAIN = "run-e2e.example";

test("company name research shows its steps on the account and the Files tab", async ({ page }) => {
  await signIn(page, await seededSlug(page, "a"), "sam", "seller");
  await page.goto("/research");
  await expect(page.getByRole("heading", { name: "Research", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Start new research" }).first().click();
  const start = page.getByRole("dialog", { name: "Start new research" });
  await start.getByRole("textbox", { name: "Company name" }).fill("Run E2E");
  await start.getByRole("textbox", { name: "Website" }).fill(DOMAIN);
  // the first press only arms the start; the second one starts it
  await start.getByRole("button", { name: /^Start research/ }).click();
  await expect(start.getByText("Are you sure?")).toBeVisible();
  await start.getByRole("button", { name: /^Yes, start/ }).click();
  await expect(start).toBeHidden();
  await expect(page).toHaveURL(/\/research\?r=/);
  await page.getByRole("link", { name: "Open account" }).first().click();
  await expect(page).toHaveURL(new RegExp(`/accounts/${DOMAIN.replace(".", "\\.")}$`));
  const live = page.getByRole("region", { name: /^Researching from the web/ });
  await expect(live.getByRole("button", { name: "Stop research" })).toBeVisible();
  await live.getByRole("link", { name: "Watch it live" }).click();
  await expect(page).toHaveURL(/\/research\?r=/);
  const detail = page.getByRole("region", { name: "Run E2E" });
  await expect(detail.getByText("Steps", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open account" }).first()).toBeVisible();

  await page.goto(`/accounts/${DOMAIN}/files`);
  await expect(page.getByRole("button", { name: "Download everything" })).toBeVisible();
});
