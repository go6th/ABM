// The quiet progress lines of Phase 4B (owner decision 2026-10-14): a large file read in parts,
// and a research resumed with what it already holds.
import { expect, test } from "vitest";
import { fileSize, partsLine } from "@/lib/documents";
import { resumingLine } from "@/components/accounts/ResumeResearch";

test("a file read in parts says which part it is on", () => {
  expect(partsLine({ stage: "parsing", parts_total: 12, parts_done: 2 })).toBe("Reading part 3 of 12");
  expect(partsLine({ stage: "parsing", parts_total: 12, parts_done: 12 })).toBe("Reading part 12 of 12");
  expect(partsLine({ stage: "parsing", parts_total: 1, parts_done: 0 })).toBeNull();
  expect(partsLine({ stage: "indexed", parts_total: 12, parts_done: 12 })).toBeNull();
});

test("sizes read in gigabytes past one", () => {
  expect(fileSize(20 * 1024 ** 3)).toBe("20.0 GB");
  expect(fileSize(3 * 1024 ** 2)).toBe("3.0 MB");
});

test("a resumed research says how much is already done", () => {
  expect(resumingLine(4, 33)).toBe("Resuming: 4 of 33 steps already done");
  expect(resumingLine(0, 0)).toBe("Resuming");
});
