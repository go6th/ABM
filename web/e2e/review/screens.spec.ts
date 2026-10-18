// Every screen in light and dark at 1440, 1024 and 390 wide, saved to docs/design-review/ as
// <screen>-<theme>-<width>.png. Signs in as client A's admin over the synthetic stack; the research
// screens need acme.example researched (the parity research spec does that).
import { expect, test, type Page } from "@playwright/test";
import { seeded, signIn, operatorSignIn, type Seeded } from "../parity/support";

const OUT = "../docs/design-review";
const WEB_ORIGIN = process.env.PARITY_WEB_ORIGIN ?? process.env.E2E_WEB_ORIGIN ?? "http://127.0.0.1:3008";
const WIDTHS = [1440, 1024, 390] as const;
const THEMES = ["light", "dark"] as const;
const ONLY = process.env.SCREENS?.split(",") ?? null;

const CLIENT: Array<[string, string]> = [
  ["home", "/"],
  ["accounts", "/accounts"],
  ["account", "/accounts/acme.example"],
  ["table-facts", "/accounts/acme.example/tables/facts"],
  ["signals", "/accounts/acme.example/history"],
  ["conversations", "/chat"],
  ["alerts", "/alerts"],
  ["runs", "/runs"],
  ["downloads", "/downloads"],
  ["settings", "/settings"],
  ["settings-users", "/settings/users"],
  ["settings-security", "/settings/security"],
  ["settings-sso", "/settings/sso"],
  ["settings-alerts", "/settings/alerts"],
  ["settings-connectors", "/settings/connectors"],
  ["settings-crm", "/settings/crm"],
  ["settings-support", "/settings/support"],
  ["settings-model-key", "/settings/model-key"],
  ["usage", "/usage"],
  ["audit", "/audit"],
  ["feedback", "/feedback"],
];
const PUBLIC: Array<[string, string]> = [["login", "/login"], ["forgot", "/forgot"], ["reset", "/reset"], ["verify-email", "/verify-email"], ["operator-login", "/operator/login"]];
const OPERATOR: Array<[string, string]> = [
  ["operator", "/operator"], ["operator-tenants", "/operator/tenants"], ["operator-configuration", "/operator/configuration"],
  ["operator-support", "/operator/support"], ["operator-runs", "/operator/runs"], ["operator-cost", "/operator/cost"],
  ["operator-spend", "/operator/spend"], ["operator-failures", "/operator/failures"],
];

let s: Seeded;
test.beforeAll(async ({ request }) => { s = await seeded(request); });

async function shoot(page: Page, name: string, path: string) {
  if (ONLY && !ONLY.some((o) => name.startsWith(o))) return;
  for (const theme of THEMES) {
    await page.context().addCookies([{ name: "ai_theme", value: theme, url: WEB_ORIGIN }]);
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await page.goto(path, { waitUntil: "domcontentloaded" });
      await expect(page.getByRole("main")).toBeVisible();
      await page.waitForTimeout(400);
      await page.screenshot({ path: `${OUT}/${name}-${theme}-${width}.png`, fullPage: true, animations: "disabled" });
    }
  }
}

test("public screens", async ({ page }) => {
  for (const [name, path] of PUBLIC) await shoot(page, name, path);
});

test("client screens", async ({ page }) => {
  await signIn(page, s.slug_a, "ada", "admin");
  // a run's page needs a run id from this workspace
  const runs = (await (await page.request.get("/v1/runs")).json()) as { runs: { run_id: string }[] };
  const run = runs.runs[0]?.run_id;
  for (const [name, path] of CLIENT) await shoot(page, name, path);
  if (run) await shoot(page, "run", `/runs/${run}`);
});

test("operator screens", async ({ page }) => {
  await operatorSignIn(page, "operator");
  for (const [name, path] of OPERATOR) await shoot(page, name, path);
});
