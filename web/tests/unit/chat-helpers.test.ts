import { describe, expect, it } from "vitest";
import { cannotAnswer, citations, numberCitations, parseFrame } from "@/components/ws/Conversation";
import { stepStates } from "@/components/ws/RunSteps";

describe("chat citations", () => {
  it("numbers fact references by first use", () => {
    const text = "Alpha [F3]. Beta [F1]. Alpha again [F3].";
    const refs = citations(text);
    expect(refs).toEqual(["F3", "F1"]);
    const out = numberCitations(text, refs, (r) => `/accounts/x/sources?ref=${r}`);
    expect(out).toBe('Alpha [1](/accounts/x/sources?ref=F3 "Source F3"). Beta [2](/accounts/x/sources?ref=F1 "Source F1"). Alpha again [1](/accounts/x/sources?ref=F3 "Source F3").');
  });

  it("recognises an answer the workspace cannot give", () => {
    expect(cannotAnswer("Acme is not held in this workspace. Open the account and click Refresh from web.")).toBe(true);
    expect(cannotAnswer("Acme hired a VP of Data [F2].")).toBe(false);
  });

  it("parses one event-stream frame", () => {
    expect(parseFrame('event: text\ndata: {"text":"hi"}')).toEqual({ event: "text", data: { text: "hi" } });
    expect(parseFrame("event: ping")).toBeNull();
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
