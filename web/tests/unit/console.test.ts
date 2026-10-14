import { describe, expect, it } from "vitest";
import { clientTone, expiresIn, isolationPill, lastActive, num, runTone, started } from "@/lib/console";
import { isBinaryUpload } from "@/lib/operator-proxy";

const NOW = new Date("2026-10-14T12:00:00Z");

describe("admin console formatting", () => {
  it("reads numbers and days as the design does", () => {
    expect(num(11000)).toBe("11,000");
    expect(lastActive(null, NOW)).toBe("Never");
    expect(lastActive("2026-10-14T08:00:00Z", NOW)).toBe("Today");
    expect(lastActive("2026-10-13T08:00:00Z", NOW)).toBe("Yesterday");
    expect(started("2026-10-14T06:00:00Z", NOW)).toBe("Today 06:00");
    expect(expiresIn(1)).toBe("Expires in 1 day");
    expect(expiresIn(0)).toBe("Expires today");
  });

  it("names the isolation check honestly, including when it never ran", () => {
    expect(isolationPill({ status: "not_run", ran_at: null, workspaces_checked: 0, failures: 0 }, NOW)).toEqual({ text: "Isolation check not run yet", tone: "neutral" });
    expect(isolationPill({ status: "passed", ran_at: "2026-10-14T02:00:00Z", workspaces_checked: 7, failures: 0 }, NOW)).toEqual({ text: "Isolation check passed · 02:00 today", tone: "ok" });
    expect(isolationPill({ status: "failed", ran_at: "2026-10-14T02:00:00Z", workspaces_checked: 7, failures: 1 }, NOW).tone).toBe("bad");
  });

  it("colours statuses", () => {
    expect(clientTone("Near credit limit")).toBe("warn");
    expect(clientTone("Invited")).toBe("neutral");
    expect(runTone("Waiting: credit limit")).toBe("warn");
    expect(runTone("Failed")).toBe("bad");
  });

  it("lets only the library upload send a file through the operator proxy", () => {
    expect(isBinaryUpload("POST", "/operator/v1/library/uploads")).toBe(true);
    expect(isBinaryUpload("POST", "/operator/v1/console/clients")).toBe(false);
    expect(isBinaryUpload("GET", "/operator/v1/library/uploads")).toBe(false);
  });
});
