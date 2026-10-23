import { describe, expect, it } from "vitest";
import { ago } from "@/components/research/ResearchBoard";
import { parseSnapshot } from "@/lib/useResearchLive";

const snapshot = {
  as_of: "2026-10-21T10:00:00+00:00",
  running: [
    {
      job_id: "j1", run_id: "r1", company: "Acme", domain: "acme.example", kind: "New research", scope: "Everything",
      topic: null, status: "running", run_status: "RUNNING", label: "Researching", open: true, can_stop: true, can_resume: false,
      step: { index: 3, total: 8, name: "Reading and grading" }, steps_done: 2, steps_total: 8,
      findings: 4, searches: 12, tokens: 52000, created_at: null, started_at: null, finished_at: null,
    },
  ],
  recent: [],
  detail: null,
};

describe("research live snapshots", () => {
  it("accepts a snapshot that matches the contract", () => {
    const parsed = parseSnapshot(JSON.stringify(snapshot));
    expect(parsed?.running[0]?.step?.name).toBe("Reading and grading");
    expect(parsed?.running[0]?.findings).toBe(4);
    expect(parsed?.running[0]?.can_resume).toBe(false);
  });

  it("ignores an event that is not JSON or does not match the contract", () => {
    expect(parseSnapshot("not json")).toBeNull();
    expect(parseSnapshot(JSON.stringify({ ...snapshot, running: [{ job_id: 1 }] }))).toBeNull();
  });

  it("accepts a snapshot from a server that does not send can_resume yet", () => {
    const older: Record<string, unknown> = { ...snapshot.running[0] };
    delete older.can_resume;
    expect(parseSnapshot(JSON.stringify({ ...snapshot, running: [older] }))?.running[0]?.can_resume).toBeUndefined();
  });
});

describe("ago", () => {
  const now = Date.parse("2026-10-21T10:00:00Z");
  it("shows nothing before the clock starts (the server render and the first paint agree)", () => {
    expect(ago("2026-10-21T09:59:00Z", null)).toBeNull();
  });
  it("reads as plain words", () => {
    expect(ago("2026-10-21T09:59:58Z", now)).toBe("just now");
    expect(ago("2026-10-21T09:59:30Z", now)).toBe("30 s ago");
    expect(ago("2026-10-21T09:55:00Z", now)).toBe("5 min ago");
    expect(ago("2026-10-21T07:00:00Z", now)).toBe("3 h ago");
    expect(ago("2026-10-18T10:00:00Z", now)).toBe("3 days ago");
  });
});
