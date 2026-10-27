// @vitest-environment jsdom
// Deleting an account asks first: the panel opens on the button, the delete button stays
// disabled until the domain is typed back exactly, and the request carries the confirmation.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const push = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh }) }));
const apiMock = vi.fn();
vi.mock("@/lib/api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class ApiError extends Error { status: number; detail: string; constructor(status: number, code: string, detail: string) { super(detail); this.status = status; this.detail = detail; } },
}));

import { DeleteAccount } from "@/components/accounts/DeleteAccount";

beforeEach(() => { push.mockReset(); refresh.mockReset(); apiMock.mockReset(); });
afterEach(() => cleanup());

test("the account is deleted only after its domain is typed back", async () => {
  apiMock.mockResolvedValue({ domain: "synthetic.example", deleted_at: "2026-10-27T10:00:00+00:00" });
  render(<DeleteAccount domain="synthetic.example" name="Synthetic Ltd" />);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Delete account" }));
  const dialog = screen.getByRole("dialog");
  const confirm = screen.getByRole("button", { name: "Delete this account" });
  expect(confirm).toHaveProperty("disabled", true);
  fireEvent.change(screen.getByLabelText("Type the domain to confirm"), { target: { value: "synthetic.examp" } });
  expect(confirm).toHaveProperty("disabled", true);
  fireEvent.change(screen.getByLabelText("Type the domain to confirm"), { target: { value: "Synthetic.example" } });
  expect(confirm).toHaveProperty("disabled", false);
  fireEvent.click(confirm);
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("DELETE", "/v1/accounts/synthetic.example", { confirm: "Synthetic.example" }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/accounts"));
  expect(dialog).toBeTruthy();
});

test("keeping the account closes the panel and sends nothing", () => {
  render(<DeleteAccount domain="synthetic.example" name="Synthetic Ltd" />);
  fireEvent.click(screen.getByRole("button", { name: "Delete account" }));
  fireEvent.click(screen.getByRole("button", { name: "Keep it" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(apiMock).not.toHaveBeenCalled();
});
