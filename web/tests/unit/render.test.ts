// Safe rendering in the client (VW4): only absolute http(s) links survive; control and direction
// characters are removed from cell text.
import { describe, expect, it } from "vitest";
import { cellText, safeLink } from "@/lib/render";

describe("safe rendering", () => {
  it("keeps only plain http and https links", () => {
    expect(safeLink("https://acme.example/careers")).toBe("https://acme.example/careers");
    for (const bad of ["javascript:alert(1)", " javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "vbscript:x",
      "//evil.test", "/relative", "https://u:p@evil.test", "https://evil.test/a b", "https://evil.test\\@good.test", "", 42, null]) {
      expect(safeLink(bad)).toBeNull();
    }
  });
  it("strips control and direction override characters", () => {
    expect(cellText("admin‮gnp.exe")).toBe("admingnp.exe");
    expect(cellText("a\u0000b\nc")).toBe("ab c");
    expect(cellText(null)).toBe("");
    expect(cellText("<script>alert(1)</script>")).toBe("<script>alert(1)</script>");
    expect(cellText("x".repeat(30), 10)).toHaveLength(10);
  });
});
