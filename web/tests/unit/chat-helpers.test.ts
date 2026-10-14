import { describe, expect, it } from "vitest";
import { parseFrame } from "@/lib/sse";
import { stepStates } from "@/components/ws/RunSteps";

describe("event stream frames", () => {
  it("parses one frame, and reports a malformed one instead of dropping it", () => {
    expect(parseFrame('event: text\ndata: {"text":"hi"}')).toEqual({ event: "text", data: { text: "hi" } });
    expect(parseFrame("event: ping")).toBeNull();
    expect(parseFrame("event: text\ndata: not json")).toEqual({ event: "invalid", data: {} });
  });
});

describe("run steps", () => {
  const progress = (done: number, current: string | null) => ({ run_id: "r", status: "RUNNING", label: "", text: "", stages_done: done, stages_total: 13, current_stage: current, finished: false });

  it("marks earlier steps done, the current step running and later steps waiting", () => {
    expect(stepStates(progress(4, "jobs"), "RUNNING")).toEqual(["done", "done", "running", "waiting", "waiting", "waiting", "waiting", "waiting"]);
  });

  it("shows the current step as paused when the run waits for approval", () => {
    expect(stepStates(progress(1, "pass1_relationship"), "AWAITING_APPROVAL")[1]).toBe("paused");
  });

  it("marks every step done on a completed run", () => {
    expect(stepStates(null, "COMPLETED").every((s) => s === "done")).toBe(true);
  });
});
