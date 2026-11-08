// @vitest-environment jsdom
// The error screen shows a reference, never the error's own text, and its button tries again.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { RouteError } from "@/ui/RouteError";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
import { ModelPreferences } from "@/components/ModelPreferences";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); refresh.mockClear(); });

test("a route error shows the standard message and a reference, not the error text", () => {
  const reset = vi.fn();
  const error = Object.assign(new Error("password=hunter2 at /srv/app/secret.ts"), { digest: "abcdef1234567" });
  const { container } = render(<RouteError error={error} reset={reset} />);
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(container.textContent).toContain("This page could not be loaded.");
  expect(container.textContent).toContain("Reference abcdef12");
  expect(container.textContent).not.toContain("hunter2");
  // without Next's ``retry`` the page is refreshed from the server, then reset
  fireEvent.click(screen.getByRole("button", { name: "Reload" }));
  expect(refresh).toHaveBeenCalledOnce();
  expect(reset).toHaveBeenCalledOnce();
});

test("Reload uses retry when Next passes it, so a server error is fetched again, not redrawn", () => {
  const reset = vi.fn();
  const retry = vi.fn();
  render(<RouteError error={new Error("x")} reset={reset} retry={retry} />);
  fireEvent.click(screen.getByRole("button", { name: "Reload" }));
  expect(retry).toHaveBeenCalledOnce();
  expect(reset).not.toHaveBeenCalled();
  expect(refresh).not.toHaveBeenCalled();
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
