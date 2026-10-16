// @vitest-environment jsdom
// The answer body is Markdown from the model: a GFM table, lists, bold and code render as real
// elements; raw HTML, images and unsafe links do not survive; citations come back as chips.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { AnswerMarkdown, inlineMarkdown, toMarkdown } from "@/components/ask/AnswerMarkdown";

afterEach(cleanup);

const chip = (n: string) => <span className="cite static" data-cite={n}>{n}</span>;

function md(text: string, inline = false) {
  return render(<AnswerMarkdown parts={[{ t: "text", v: text }]} cite={chip} inline={inline} />);
}

test("a table, a list, bold and code render as a table, a list, bold and code", () => {
  const { container } = md(
    "Here is the picture:\n\n| Person | Role |\n|---|:---:|\n| Jane | **CTO** |\n| Bob | VP Ops |\n\n" +
      "- first\n- second\n\n```sql\nSELECT 1;\n```\n\nUse `kubectl` and read > carefully.\n\n> A quote.",
  );
  const table = container.querySelector("table");
  expect(table).not.toBeNull();
  expect(table?.parentElement?.className).toBe("md-table");
  expect(container.querySelectorAll("th").length).toBe(2);
  expect(container.querySelectorAll("tbody tr").length).toBe(2);
  expect(container.querySelector("td strong")?.textContent).toBe("CTO");
  expect(Array.from(container.querySelectorAll("ul li")).map((li) => li.textContent)).toEqual(["first", "second"]);
  expect(container.querySelector("pre code")?.textContent).toBe("SELECT 1;\n");
  expect(container.querySelector("p code")?.textContent).toBe("kubectl");
  expect(container.querySelector("blockquote")?.textContent).toContain("A quote.");
  // the CSP forbids inline styles: an aligned column is a class, never a style attribute
  expect(container.innerHTML).not.toContain("style=");
  expect(container.querySelectorAll("th.md-c, td.md-c").length).toBe(3);
});

test("raw HTML, images and scripts are stripped; nothing runs", () => {
  const { container } = md(
    "Before <script>window.pwned = 1</script> <img src=x onerror=alert(1)> ![alt](https://evil.example/p.png) after\n\n<div onclick=\"alert(1)\">block</div>",
  );
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container.innerHTML).not.toContain("<img");
  expect(container.innerHTML).not.toContain("onerror");
  expect(container.innerHTML).not.toContain("onclick");
  expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
  expect(container.textContent).toContain("Before");
  expect(container.textContent).toContain("after");
});

test("an http(s) link opens in a new tab without a referrer; other schemes are plain text", () => {
  const { container } = md("[site](https://acme.example/x) [bad](javascript:alert(1)) [rel](//evil.example/y) [mail](mailto:a@b.c)");
  const links = container.querySelectorAll("a");
  expect(links.length).toBe(1);
  expect(links[0]?.getAttribute("href")).toBe("https://acme.example/x");
  expect(links[0]?.getAttribute("target")).toBe("_blank");
  expect(links[0]?.getAttribute("rel")).toBe("noopener noreferrer");
  expect(container.textContent).toContain("bad");
  expect(container.textContent).toContain("rel");
  expect(container.textContent).toContain("mail");
});

test("citation and bold parts come back as the chip and as bold, inside a table too", () => {
  const parts = [
    { t: "text", v: "| Account | Fact |\n|---|---|\n| " },
    { t: "bold", v: "Acme" },
    { t: "text", v: " | hiring " },
    { t: "cite", v: "2" },
    { t: "text", v: " |" },
  ];
  expect(toMarkdown(parts)).toBe("| Account | Fact |\n|---|---|\n| **Acme** | hiring [2](#cite-2) |");
  const { container } = render(<AnswerMarkdown parts={parts} cite={chip} />);
  expect(container.querySelector("td strong")?.textContent).toBe("Acme");
  const c = container.querySelector("td [data-cite]");
  expect(c?.textContent).toBe("2");
  expect(c?.tagName).toBe("SPAN");
  expect(container.querySelector("a")).toBeNull();
});

test("a partial answer renders as it arrives: an unfinished table is still readable text", () => {
  const { container } = md("Comparing them:\n\n| Person | Role |\n|---|---|\n| Jane |");
  expect(container.textContent).toContain("Comparing them:");
  expect(container.textContent).toContain("Jane");
});

test("an item line keeps its number and renders its bold without becoming a list", () => {
  expect(inlineMarkdown("1. **Ask** about the migration")).toBe("1\\. **Ask** about the migration");
  expect(inlineMarkdown("- a bullet")).toBe("\\- a bullet");
  expect(inlineMarkdown("## heading")).toBe("\\## heading");
  const { container } = md("1. **Ask** about the migration", true);
  expect(container.querySelector("ol")).toBeNull();
  expect(container.querySelector("p")).toBeNull();
  expect(container.querySelector("strong")?.textContent).toBe("Ask");
  expect(container.textContent).toBe("1. Ask about the migration");
  expect(screen.getByText("Ask").tagName).toBe("STRONG");
});
