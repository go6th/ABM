// Output parity on a built bundle version: a research run driven to its end over the synthetic
// world (PARITY_BUNDLE=on in the harness) leaves a built version; the account page then shows
// one tab per delivered file whose every table has the headers, row count, order and cell
// text of the rendered file's table (read back from the downloaded bytes by
// python -m parity.tables dump), every count on screen equals the manifest's recalculated
// value, each downloaded file's SHA-256 equals the manifest, and "Download all" is the
// workspace zip, which never holds the internal-only file.
import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { mismatches, summaryValues, type CountBundle } from "../lib/parity/counts";
import { signIn, API } from "./helpers";

const DOMAIN = "parity-e2e.example";
const REPO = path.resolve(__dirname, "..", "..");
test.setTimeout(600_000);

interface Dumped { sheet?: string; headers: string[]; rows: string[][] }

function dumpTables(file: string): Dumped[] {
  const done = spawnSync("uv", ["run", "python", "-m", "parity.tables", "dump", file], { cwd: REPO, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  expect(done.status, done.stderr).toBe(0);
  return JSON.parse(done.stdout) as Dumped[];
}

function zipMembers(file: string): string[] {
  const done = spawnSync("python3", ["-c", "import sys,zipfile;print('\\n'.join(zipfile.ZipFile(sys.argv[1]).namelist()))", file], { encoding: "utf8" });
  expect(done.status, done.stderr).toBe(0);
  return done.stdout.trim().split("\n");
}

/** Every row of one table on screen, paging through "Next" until it is disabled. */
async function screenTable(page: Page, id: string): Promise<{ headers: string[]; rows: string[][] }> {
  const section = page.locator(`[data-table="${id}"]`);
  await expect(section).toBeVisible();
  const total = Number(await section.getAttribute("data-total"));
  const headers = await section.locator("thead th span.th").allTextContents();
  if (total === 0) return { headers, rows: [] };
  await expect(section.locator("tbody tr").first()).toBeVisible();
  const rows: string[][] = [];
  for (;;) {
    const page_rows = section.locator("tbody tr");
    const n = await page_rows.count();
    for (let i = 0; i < n; i++) {
      rows.push(await page_rows.nth(i).locator("td:not(.rn)").allTextContents());
    }
    const next = section.getByRole("button", { name: "Next" });
    if ((await next.count()) === 0 || (await next.isDisabled())) break;
    const before = await section.locator("tbody tr td.rn").first().textContent();
    await next.click();
    await expect(section.locator("tbody tr td.rn").first()).not.toHaveText(before ?? "");
  }
  expect(rows.length).toBe(total);
  return { headers, rows };
}

test("the screens, the stored data and the downloads agree", async ({ page }) => {
  const seeded = await (await page.request.get(`${API}/fake-idp/tenants`)).json();
  await signIn(page, seeded.slug_a, "ada", "admin");
  // the run, created as the company form once did (the research form queues a job the harness
  // does not run); the harness then drives the run to its end over the synthetic world
  const session = await (await page.request.get("/v1/sso/session")).json();
  const created = await page.request.post("/v1/runs", {
    data: { input_mode: "company", company: { company_name: "Parity E2E", domain: DOMAIN }, domain: DOMAIN, mode: "full" },
    headers: { Origin: new URL(page.url()).origin, "X-CSRF-Token": session.csrf_token ?? "", "Idempotency-Key": randomUUID() },
  });
  expect(created.status(), await created.text()).toBe(202);
  const runId = (await created.json()).run_id as string;
  expect(runId).toBeTruthy();
  const driven = await page.request.post(`${API}/fake-idp/drive`, { data: { run_id: runId }, timeout: 540_000 });
  expect(driven.ok(), await driven.text()).toBeTruthy();

  const base = `/v1/accounts/${DOMAIN}/parity`;
  const versions = await (await page.request.get(`${base}/versions`)).json();
  expect(versions.versions[0], JSON.stringify(versions)).toMatchObject({ version: 1, status: "built" });
  const manifest = await (await page.request.get(`${base}/manifest?version=1`)).json();
  const screen = await (await page.request.get(`${base}/screen?version=1`)).json();
  const bundle = (await (await page.request.get(`${base}/bundle?version=1`)).json()).bundle;

  // every count the shared module computes equals the manifest's recalculated value
  const countBundle: CountBundle = { meta: screen.meta, jobs_rows: screen.count_rows.jobs_rows, people_rows: screen.count_rows.people_rows, ranking: screen.ranking };
  const computed = summaryValues(countBundle);
  expect(mismatches(computed, screen.summary)).toEqual([]);
  expect(Object.keys(manifest.summary).length).toBe(3);
  let compared = 0;
  for (const values of Object.values(manifest.summary) as Record<string, unknown>[]) {
    for (const [key, got] of Object.entries(values)) {
      const want = computed[key];
      if (want === undefined) continue; // the workbook check's own metadata (formula counts)
      compared += 1;
      if (typeof want === "number") expect(Math.abs(Number(got) - want), key).toBeLessThan(1e-9);
      else expect(String(got ?? ""), key).toBe(want);
    }
  }
  expect(compared).toBeGreaterThan(20);
  // the stored bundle and the screen carry the same numbers
  expect(screen.computed).toEqual(bundle.computed);

  // every file downloads with the manifest's digest; the tables inside equal the screen's
  const dir = mkdtempSync(path.join(tmpdir(), "parity-e2e-"));
  const dumped: Record<string, Dumped[]> = {};
  for (const f of manifest.files as { name: string; size: number; sha256: string; mime: string }[]) {
    const r = await page.request.get(`${base}/files/${encodeURIComponent(f.name)}?version=1`);
    expect(r.status(), f.name).toBe(200);
    expect(r.headers()["content-disposition"]).toBe(`attachment; filename="${f.name}"`);
    expect(r.headers()["content-type"]).toContain(f.mime);
    const body = await r.body();
    expect(body.length, f.name).toBe(f.size);
    expect(createHash("sha256").update(body).digest("hex"), f.name).toBe(f.sha256);
    const local = path.join(dir, f.name);
    writeFileSync(local, body);
    if (f.name.endsWith(".docx") || f.name.endsWith(".xlsx")) dumped[f.name] = dumpTables(local);
    if (f.name.endsWith("_workspace.zip")) {
      const members = zipMembers(local);
      expect(members.some((m) => m.includes("Internal_Only"))).toBe(false);
      expect(members).toContain("RESTORE.md");
    }
  }

  await page.goto(`/accounts/${DOMAIN}`);
  const section = page.locator("section.parity");
  await expect(section).toBeVisible();
  expect(await section.getByTestId("download-all").getAttribute("href")).toContain("_workspace.zip?version=1");
  const tabs = section.getByRole("navigation", { name: "Delivered files" });
  const tabKeys = await tabs.locator("[data-file-tab]").evaluateAll((els) => els.map((e) => e.getAttribute("data-file-tab")));
  const fileOf = (kind: string) => (screen.files as { name: string; file: string | null }[]).find((f) => f.file === kind)!.name;
  for (const kind of tabKeys as string[]) {
    await tabs.getByRole("tab", { name: (await tabs.locator(`[data-file-tab="${kind}"]`).textContent()) ?? "" }).click();
    const panel = section.locator(`[data-file="${kind}"]`);
    await expect(panel).toBeVisible();
    expect(await panel.getByTestId(`download-${kind}`).getAttribute("href")).toBe(`/v1/accounts/${DOMAIN}/parity/files/${encodeURIComponent(fileOf(kind))}?version=1`);
    if (kind === "people" || kind === "jobs" || kind === "stack") await expect(panel.getByTestId("counts-ok")).toBeVisible();
    const fileTables = dumped[fileOf(kind)] ?? [];
    const ids = await panel.locator("[data-table]:not([data-table^='counts_'])").evaluateAll((els) => els.map((e) => e.getAttribute("data-table")));
    if (kind === "stack") {
      const bySheet = Object.fromEntries(fileTables.map((t) => [t.sheet, t]));
      for (const id of ids as string[]) {
        const got = await screenTable(page, id);
        const sheet = (screen.tables as { id: string; sheet: string }[]).find((t) => t.id === id)!.sheet;
        expect(got.headers, id).toEqual(bySheet[sheet]!.headers);
        expect(got.rows, id).toEqual(bySheet[sheet]!.rows);
      }
    } else if (kind === "people" || kind === "jobs") {
      const got = await screenTable(page, kind);
      expect(got.headers).toEqual(fileTables[0]!.headers);
      expect(got.rows).toEqual(fileTables[0]!.rows);
    } else if (kind === "summary" || kind === "evidence") {
      expect(ids.length, kind).toBe(fileTables.length);
      for (const [i, id] of (ids as string[]).entries()) {
        const got = await screenTable(page, id);
        expect(got.headers, id).toEqual(fileTables[i]!.headers);
        expect(got.rows, id).toEqual(fileTables[i]!.rows);
      }
    }
  }
});
