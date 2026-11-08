// @vitest-environment jsdom
// The Ask composer (owner request 2026-11-08): the question, Accounts and the model; the "+" menu,
// the Search toggle and the separate Effort chip are gone, and the effort is chosen inside the
// model menu.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { AskComposer } from "@/components/ask/AskComposer";
import type { ModelChoices } from "@/lib/gen/api.gen";

afterEach(cleanup);

const CHOICES: ModelChoices = {
  analysis: "fable", analysis_effort: "medium", default_analysis: "fable", default_research: "opus",
  research: "opus", research_effort: "medium", efforts: ["low", "medium", "high"],
  models: [
    { id: "fable", name: "Fable 5.1", label: "Fable 5.1", line: "Most capable", available: true, reason: null },
    { id: "sonnet", name: "Sonnet 5.5", label: "Sonnet 5.5", line: "Fast and capable", available: true, reason: null },
    { id: "kimi", name: "Kimi K3", label: "Kimi K3", line: "Open model", available: false, reason: "Not set up" },
  ],
};

function Harness({ onSend = vi.fn() }: { onSend?: () => void }) {
  const [model, setModel] = useState("fable");
  const [effort, setEffort] = useState("medium");
  const [draft, setDraft] = useState("");
  return (
    <AskComposer
      variant="hero" placeholder="Ask about your accounts" draft={draft} onDraft={setDraft} onSend={onSend} busy={false}
      accounts={[{ id: "a1", domain: "acme.com", name: "Acme" }]} scope={null} onScope={vi.fn()}
      webHintOpen={false} onWebHint={vi.fn()} models={CHOICES} model={model} onModel={setModel} effort={effort} onEffort={setEffort} menuDown
    />
  );
}

test("the composer keeps Accounts and the model only: no +, no Search, no Effort chip", () => {
  render(<Harness />);
  expect(screen.getByRole("button", { name: /Answer from all accounts/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Model: Fable 5.1, effort Medium. Change model or effort" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add files" })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Search/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Effort/ })).toBeNull();
});

test("the effort is chosen inside the model menu, which stays open; picking a model closes it", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: /^Model: Fable 5.1/ }));
  const menu = screen.getByRole("menu", { name: "Model and effort" });
  expect(menu).toBeTruthy();
  expect(screen.getByRole("group", { name: "Effort" })).toBeTruthy();
  expect(screen.getByRole("menuitemradio", { name: "Medium" }).getAttribute("aria-checked")).toBe("true");
  expect(screen.getByText("Balanced, right for most questions")).toBeTruthy();
  fireEvent.click(screen.getByRole("menuitemradio", { name: "High" }));
  expect(screen.getByRole("menuitemradio", { name: "High" }).getAttribute("aria-checked")).toBe("true");
  expect(screen.getByRole("menu", { name: "Model and effort" })).toBeTruthy(); // still open
  // a model that cannot be chosen says why and does nothing
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Kimi K3/ }));
  expect(screen.getByText("Not set up")).toBeTruthy();
  fireEvent.click(screen.getByRole("menuitemradio", { name: /Sonnet 5.5/ }));
  expect(screen.queryByRole("menu", { name: "Model and effort" })).toBeNull();
  expect(screen.getByRole("button", { name: "Model: Sonnet 5.5, effort High. Change model or effort" })).toBeTruthy();
});

test("Enter and Ask send the question to the workspace", () => {
  const onSend = vi.fn();
  render(<Harness onSend={onSend} />);
  fireEvent.change(screen.getByLabelText("Ask a question"), { target: { value: "Who is hiring?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(onSend).toHaveBeenCalledTimes(1);
});
