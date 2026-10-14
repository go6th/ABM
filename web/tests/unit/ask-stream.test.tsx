// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { addPieces } from "@/lib/sse";
import { StreamingAnswer } from "@/components/ask/AnswerView";

afterEach(cleanup);

test("streamed pieces build paragraphs in order and malformed frames change nothing", () => {
  let partial = addPieces([], { p: 0, parts: [{ t: "text", v: "Acme is hiring " }, { t: "cite", v: "1" }] });
  partial = addPieces(partial, { p: 0, parts: [{ t: "text", v: " and building." }] });
  partial = addPieces(partial, { p: 1, parts: [{ t: "text", v: "Next." }] });
  expect(partial).toEqual([
    [{ t: "text", v: "Acme is hiring " }, { t: "cite", v: "1" }, { t: "text", v: " and building." }],
    [{ t: "text", v: "Next." }],
  ]);
  expect(addPieces(partial, { p: 5, parts: [] })).toBe(partial);
  expect(addPieces(partial, { p: "0", parts: [] })).toBe(partial);
  expect(addPieces(partial, { p: 0, parts: [{ t: 1 }, null] })).toEqual(partial);
});

test("a streaming answer shows citation numbers that cannot be clicked yet", () => {
  render(<StreamingAnswer paragraphs={[[{ t: "text", v: "Acme is hiring " }, { t: "cite", v: "1" }]]} />);
  expect(screen.getByText("Writing the answer")).toBeTruthy();
  expect(screen.getByLabelText("Source 1").tagName).toBe("SPAN");
  expect(screen.queryByRole("button")).toBeNull();
});
