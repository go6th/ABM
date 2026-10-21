import { describe, expect, it } from "vitest";
import { parseFrame } from "@/lib/sse";
import { currentStep, runSteps, stepStates } from "@/components/ws/RunSteps";

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

  it("uses the server's own steps and finds the step the run is on", () => {
    const steps = [
      { id: "setup", name: "Set up", state: "done" },
      { id: "search", name: "Web research", state: "done" },
      { id: "read", name: "Reading and grading", state: "running", rows_done: 12, rows_total: 33 },
      { id: "write", name: "Writing up", state: "waiting" },
    ];
    const p = { ...progress(2, "Reading and grading"), steps };
    expect(runSteps(p, "RUNNING").map((st) => st.name)).toEqual(["Set up", "Web research", "Reading and grading", "Writing up"]);
    expect(currentStep(p, "RUNNING")).toEqual({ index: 2, step: steps[2], total: 4 });
    const ended = { ...p, steps: steps.map((st, i) => ({ ...st, state: i < 2 ? "done" : i === 2 ? "stopped" : "not_reached" })) };
    expect(currentStep(ended, "CANCELED")?.step.state).toBe("stopped");
    expect(currentStep({ ...p, steps: steps.map((st) => ({ ...st, state: "done" })) }, "COMPLETED")).toBeNull();
  });
});
