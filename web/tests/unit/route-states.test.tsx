// @vitest-environment jsdom
// The error screen shows a reference, never the error's own text, and its button tries again.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { RouteError } from "@/ui/RouteError";
import { ModelPreferences } from "@/components/ModelPreferences";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("a route error shows the standard message and a reference, not the error text", () => {
  const reset = vi.fn();
  const error = Object.assign(new Error("password=hunter2 at /srv/app/secret.ts"), { digest: "abcdef1234567" });
  const { container } = render(<RouteError error={error} reset={reset} />);
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(container.textContent).toContain("This page could not be loaded.");
  expect(container.textContent).toContain("Reference abcdef12");
  expect(container.textContent).not.toContain("hunter2");
  fireEvent.click(screen.getByRole("button", { name: "Reload" }));
  expect(reset).toHaveBeenCalledOnce();
});

test("without a digest there is no reference line", () => {
  const { container } = render(<RouteError error={new Error("x")} reset={() => undefined} />);
  expect(container.textContent).not.toContain("Reference");
});

test("model preferences show a loading skeleton, then an error card when the models cannot be loaded", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ title: "down" }), { status: 503 })));
  const { container } = render(<ModelPreferences />);
  expect(container.querySelector(".skel")).not.toBeNull();
  await waitFor(() => expect(screen.getByText("Model preferences could not be loaded")).toBeTruthy());
  expect(container.querySelector(".skel")).toBeNull();
});
