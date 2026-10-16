// The quiet progress lines of Phase 4B (owner decision 2026-10-14): a large file read in parts,
// and a research resumed with what it already holds.
import { expect, test } from "vitest";
import { fileSize, filesLine, indexingLine, partsLine, progressLine } from "@/lib/documents";
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

test("the indexing line counts passages indexed so far, and the progress line prefers it", () => {
  expect(indexingLine({ stage: "indexing", passages: 22380, indexed_passages: 3200 })).toBe("Indexing 3,200 of 22,380 passages");
  expect(indexingLine({ stage: "indexing", passages: 22380, indexed_passages: 0 })).toBe("Indexing 0 of 22,380 passages");
  expect(indexingLine({ stage: "indexed", passages: 22380, indexed_passages: 22380 })).toBeNull();
  expect(indexingLine({ stage: "parsing", passages: 0, indexed_passages: 0 })).toBeNull();
  expect(progressLine({ stage: "parsing", passages: 0, indexed_passages: 0, parts_total: 3, parts_done: 1 })).toBe("Reading part 2 of 3");
  expect(progressLine({ stage: "indexing", passages: 10, indexed_passages: 4, parts_total: 3, parts_done: 3 })).toBe("Indexing 4 of 10 passages");
});

test("the files line counts a zip's members read and skipped", () => {
  expect(filesLine({ members_read: 269, members_skipped: [{ member: "a.png", reason: "not a document type" }] })).toBe("269 files read, 1 skipped");
  expect(filesLine({ members_read: 1, members_skipped: [] })).toBe("1 file read");
  expect(filesLine({ members_read: 0, members_skipped: [] })).toBeNull();
});
