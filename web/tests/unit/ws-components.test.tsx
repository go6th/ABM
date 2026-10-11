// @vitest-environment jsdom
// The workspace's signature components: grade marks, citation chips and the evidence drawer,
// the "what this answer used" panel, the web offer's single charge, the composer's keys, the
// cap meter, and untrusted model output rendered inert.
import { cleanup, fireEvent, render, screen, act, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useState } from "react";
import { GradeMark, toGrade, wedgePath } from "@/components/ws/GradeMark";
import { CitedText } from "@/components/ws/CitedText";
import { EvidenceDrawer } from "@/components/ws/EvidenceDrawer";
import type { Evidence } from "@/components/ws/evidence";
import { UsedPanel } from "@/components/ws/UsedPanel";
import { WebOffer } from "@/components/ws/WebOffer";
import { Composer } from "@/components/ws/Composer";
import { Answer, parseFrame } from "@/components/ws/Conversation";
import { CapMeter } from "@/components/ws/WorkspaceShell";

vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));

afterEach(cleanup);

const EVIDENCE: Evidence[] = [
  { n: 1, grade: "confirmed", reason: "Read on the careers page.", title: "Cloud lead", sourceName: "careers.example", retrieved: "6 October", excerpt: "Lead the cloud migration of clinical systems.", span: [9, 30], url: "https://careers.example/1" },
  { n: 2, grade: "hypothesis", reason: null, title: "Capital plan", sourceName: "Annual report", retrieved: null, excerpt: null, span: null, url: null },
];

test("grade marks always carry the plain label and never a raw score", () => {
  render(<><GradeMark grade="probable" /><GradeMark grade="lead" showLabel={false} /></>);
  expect(screen.getByText("Probable")).toBeTruthy();
  expect(screen.getByText("Lead").className).toBe("ws-sr");
  expect(toGrade("Confirmed")).toBe("confirmed");
  expect(toGrade("A")).toBeNull();
  expect(toGrade(0.82)).toBeNull();
  expect(wedgePath(0.25)).toMatch(/A3\.2 3\.2 0 0 1 8\.200 5\.000Z$/);
  expect(wedgePath(0.75)).toContain(" 0 1 1 ");
});

function Harness() {
  const [open, setOpen] = useState<number | null>(null);
  const [chip, setChip] = useState<HTMLButtonElement | null>(null);
  return (
    <div id="ws-root">
      <p><CitedText text="The team leads it [1]. Funding is likely [2]. Unknown [7]." evidence={EVIDENCE} pressed={open} onOpen={(n, el) => { setChip(el); setOpen(n); }} /></p>
      <EvidenceDrawer items={EVIDENCE} open={open} onOpenChange={setOpen} onPick={setOpen} returnFocus={chip} />
    </div>
  );
}

test("citation chips are numbered buttons with their grade, and unknown markers stay text", () => {
  render(<Harness />);
  expect(screen.getByRole("button", { name: "Source 1, Confirmed" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Source 2, Hypothesis" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Source 7/ })).toBeNull();
  expect(screen.getByText(/Unknown \[7\]/)).toBeTruthy();
});

test("a chip opens the drawer; Esc closes it and focus returns to the chip", async () => {
  render(<Harness />);
  const chip = screen.getByRole("button", { name: "Source 1, Confirmed" });
  chip.focus();
  await act(async () => { fireEvent.click(chip); });
  expect(chip.getAttribute("aria-pressed")).toBe("true");
  const dialog = screen.getByRole("dialog");
  expect(dialog.textContent).toContain("Read on the careers page.");
  expect(dialog.querySelector("mark")?.textContent).toBe("cloud migration of cl");
  const link = screen.getByRole("link", { name: /Open source/ });
  expect(link.getAttribute("rel")).toBe("noopener noreferrer nofollow");
  expect(link.getAttribute("target")).toBe("_blank");
  await act(async () => { fireEvent.keyDown(dialog, { key: "Escape" }); await new Promise((r) => setTimeout(r, 0)); });
  expect(screen.queryByRole("dialog")).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(chip));
});

test("a source with a dead link reads Archive record", async () => {
  render(<Harness />);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Source 2, Hypothesis" })); });
  expect(screen.getByText("Archive record")).toBeTruthy();
  expect(screen.queryByRole("link", { name: /Open source/ })).toBeNull();
});

test("the used panel summarises in one line and expands to three rows", () => {
  render(<UsedPanel used={{ facts: 41, updated: "6 October", web: null, mail: null }} />);
  expect(screen.getByText("41 facts held here, web not used")).toBeTruthy();
  expect(screen.getByText("Not used")).toBeTruthy();
  expect(screen.getByText("Not connected")).toBeTruthy();
});

test("confirming a web refresh charges once: double click sends one key, a retry reuses it", async () => {
  const keys: string[] = [];
  let fail = true;
  const onConfirm = vi.fn(async (key: string) => {
    keys.push(key);
    await new Promise((r) => setTimeout(r, 10));
    if (fail) throw new Error("network");
  });
  render(<WebOffer offer={{ what: "Newer postings may exist.", scope: "Job postings only", minutes: 4, credits: 6, left: 184 }} onConfirm={onConfirm} onStay={() => undefined} />);
  const button = screen.getByRole("button", { name: "Confirm refresh" });
  await act(async () => { fireEvent.click(button); fireEvent.click(button); });
  expect(onConfirm).toHaveBeenCalledTimes(1);
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  fail = false;
  await act(async () => { fireEvent.click(button); await new Promise((r) => setTimeout(r, 20)); });
  expect(onConfirm).toHaveBeenCalledTimes(2);
  expect(keys[0]).toBe(keys[1]);
  expect(keys[0]).toMatch(/^[0-9a-f]{32}$/);
});

test("the composer sends on Enter, adds a line on Shift+Enter, and shows Stop while streaming", () => {
  const onSend = vi.fn();
  const { rerender } = render(<Composer value="Who leads IT?" onChange={() => undefined} onSend={onSend} streaming={false} placeholder="Ask" />);
  const box = screen.getByLabelText("Your question");
  fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
  expect(onSend).not.toHaveBeenCalled();
  fireEvent.keyDown(box, { key: "Enter" });
  expect(onSend).toHaveBeenCalledTimes(1);
  const onStop = vi.fn();
  rerender(<Composer value="" onChange={() => undefined} onSend={onSend} onStop={onStop} streaming placeholder="Ask" />);
  fireEvent.click(screen.getByRole("button", { name: "Stop answer" }));
  expect(onStop).toHaveBeenCalled();
  expect(screen.getByText("Answers use data held in this workspace. The web is used only after you confirm.")).toBeTruthy();
});

test("model output renders inert: no raw HTML, no script, no unsafe links", () => {
  const hostile = [
    "<script>window.pwned = 1</script>",
    "<img src=x onerror=alert(1)>",
    "[click](javascript:alert(1)) and [ok](https://example.com/a)",
    "![tracker](https://evil.example/p.png)",
  ].join("\n\n");
  const { container } = render(<Answer text={hostile} streaming={false} />);
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container.innerHTML).not.toContain("onerror");
  const links = [...container.querySelectorAll("a")];
  expect(links.map((a) => a.getAttribute("href"))).toEqual(["https://example.com/a"]);
  expect(links[0]?.getAttribute("rel")).toBe("noopener noreferrer nofollow");
  expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
});

test("stream frames parse event and JSON data, and junk is dropped", () => {
  expect(parseFrame('event: text\ndata: {"text":"Hi"}')).toEqual({ event: "text", data: { text: "Hi" } });
  expect(parseFrame("event: text\ndata: not json")).toBeNull();
  expect(parseFrame("event: ping")).toBeNull();
});

test("the cap meter reads as a count and marks the full state", () => {
  const { container, rerender } = render(<CapMeter used={12} limit={34} />);
  expect(screen.getByText("12 of 34 accounts")).toBeTruthy();
  expect(container.querySelector("[data-full]")?.getAttribute("data-full")).toBe("false");
  rerender(<CapMeter used={34} limit={34} />);
  expect(container.querySelector("[data-full]")?.getAttribute("data-full")).toBe("true");
  expect(container.textContent).toContain("You have reached your account limit.");
});
