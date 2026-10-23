// @vitest-environment jsdom
// Mail and drive (screen 8): the sources are grouped by provider, each card shows its status pill,
// last sync, matched items and the last error as a plain sentence (never a code), Sync now shows
// its result inline, and Disconnect asks first.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

const api = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh }) }));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public readonly status: number, public readonly type: string, public readonly detail: string) {
      super(`${status} ${type}`);
    }
  }
  return { api: (...args: unknown[]) => api(...args), ApiError };
});

import { errorSentence, MailDrive, type MailDriveSourceView, type MailDriveView, relative, SourceCard } from "@/components/accounts/MailDrive";
import { ApiError } from "@/lib/api";
import { m } from "@/lib/messages";

afterEach(() => {
  cleanup();
  api.mockReset();
  refresh.mockReset();
  vi.restoreAllMocks();
});

const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

function source(kind: string, over: Partial<MailDriveSourceView> = {}): MailDriveSourceView {
  const labels: Record<string, [string, "google" | "microsoft", "mail" | "drive"]> = {
    gmail: ["Gmail", "google", "mail"],
    google_drive: ["Google Drive", "google", "drive"],
    outlook: ["Outlook", "microsoft", "mail"],
    onedrive: ["OneDrive", "microsoft", "drive"],
    sharepoint: ["SharePoint", "microsoft", "drive"],
  };
  const [label, family, group] = labels[kind]!;
  return {
    kind, label, family, group, available: true, status: "not_connected", accounts: 0, items: 0,
    last_synced_at: null, broken_reason: null, last_sync_status: null, last_error: null,
    last_error_at: null, last_sync_listed: null, last_sync_matched: null, ...over,
  };
}

const card = (label: string) => screen.getByRole("region", { name: label });

test("sources are grouped under Google and Microsoft with the privacy note", () => {
  const family = { status: "not_connected", kind: null, source: null, options: [], accounts: 0, items: 0, last_synced_at: null };
  const view: MailDriveView = {
    allowed: true, mail: family, drive: family,
    sources: [source("gmail"), source("google_drive"), source("outlook", { available: false, status: "not_available" })],
  };
  render(<MailDrive view={view} />);
  expect(screen.getByRole("heading", { name: "Google" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Microsoft" })).toBeTruthy();
  expect(screen.getByText(m.mailDrive.privacy)).toBeTruthy();
  expect(within(card("Gmail")).getByRole("button", { name: "Connect" })).toBeTruthy();
  const outlook = card("Outlook");
  expect(within(outlook).getByText("Not available yet")).toBeTruthy();
  expect(within(outlook).queryAllByRole("button")).toHaveLength(0);
  expect(screen.queryByRole("region", { name: "OneDrive" })).toBeNull(); // only what the API returned
});

test("a connected source shows its last sync, its matches, Sync now and Disconnect", () => {
  render(<SourceCard source={source("gmail", { status: "connected", items: 3, accounts: 2, last_synced_at: minutesAgo(5), last_sync_status: "ok" })} />);
  const gmail = card("Gmail");
  expect(within(gmail).getByText("Connected").className).toContain("pill ok");
  expect(within(gmail).getByText("Last synced 5 minutes ago")).toBeTruthy();
  expect(within(gmail).getByText("3 items matched to your accounts")).toBeTruthy();
  expect(within(gmail).getByText("Shown only to: You")).toBeTruthy();
  expect(within(gmail).getByRole("button", { name: "Sync now" })).toBeTruthy();
  expect(within(gmail).getByRole("button", { name: "Disconnect" })).toBeTruthy();
  expect(within(gmail).queryByRole("alert")).toBeNull();
});

test("a broken grant needs attention and says why in a plain sentence", () => {
  render(<SourceCard source={source("onedrive", { status: "broken", broken_reason: "token_revoked", last_synced_at: minutesAgo(180) })} />);
  const drive = card("OneDrive");
  expect(within(drive).getByText("Needs attention").className).toContain("pill warn");
  expect(within(drive).getByRole("alert").textContent).toBe("Access was removed in your Microsoft account. Reconnect.");
  expect(within(drive).getByRole("button", { name: "Reconnect" })).toBeTruthy();
  expect(within(drive).getByRole("button", { name: "Disconnect" })).toBeTruthy();
  expect(within(drive).queryByRole("button", { name: "Sync now" })).toBeNull();
  expect(within(drive).getByText("Last synced 3 hours ago")).toBeTruthy();
});

test("a transient error on a connected source is shown until a clean sync", () => {
  render(<SourceCard source={source("gmail", { status: "connected", last_sync_status: "failed", last_error: "provider_unavailable" })} />);
  expect(screen.getByRole("alert").textContent).toBe("The provider did not answer. It retries automatically.");
  cleanup();
  render(<SourceCard source={source("gmail", { status: "connected", last_sync_status: "ok", last_error: "provider_unavailable" })} />);
  expect(screen.queryByRole("alert")).toBeNull();
});

test("error codes become sentences, with a sensible default", () => {
  expect(errorSentence("token_revoked", "google")).toBe("Access was removed in your Google account. Reconnect.");
  expect(errorSentence("scope_changed", "google")).toBe("Some permissions were not granted. Reconnect and allow read access.");
  expect(errorSentence("scope_mismatch", "microsoft")).toBe("Some permissions were not granted. Reconnect and allow read access.");
  expect(errorSentence("token_refresh_failed", "google")).toBe("The provider did not answer. It retries automatically.");
  expect(errorSentence("rate_limited", "google")).toBe(m.mailDrive.errors.rateLimited);
  expect(errorSentence("something_new", "google")).toBe(m.mailDrive.errors.other);
});

test("relative times read as people say them", () => {
  const now = new Date("2026-10-23T12:00:00Z");
  expect(relative("2026-10-23T11:59:40Z", now)).toBe("just now");
  expect(relative("2026-10-23T11:59:00Z", now)).toBe("1 minute ago");
  expect(relative("2026-10-23T10:00:00Z", now)).toBe("2 hours ago");
  expect(relative("2026-10-22T10:00:00Z", now)).toBe("yesterday");
  expect(relative("2026-10-20T10:00:00Z", now)).toBe("3 days ago");
});

test("Disconnect asks first and does nothing when the user cancels", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<SourceCard source={source("gmail", { status: "connected" })} />);
  fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
  expect(confirm).toHaveBeenCalledWith("Disconnect Gmail? Synced items are deleted from your workspace.");
  expect(api).not.toHaveBeenCalled();

  confirm.mockReturnValue(true);
  api.mockResolvedValue({ disconnected: 1, items_deleted: 4 });
  fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
  await waitFor(() => expect(refresh).toHaveBeenCalled());
  expect(api).toHaveBeenCalledWith("POST", "/v1/mail-and-drive/gmail/disconnect");
});

test("Sync now syncs the family and shows what it found inline", async () => {
  api.mockResolvedValue({
    family: "google",
    sources: [
      { kind: "gmail", label: "Gmail", state: "ok", listed: 9, matched: 4, reason: null },
      { kind: "google_drive", label: "Google Drive", state: "ok", listed: 2, matched: 0, reason: null },
    ],
  });
  render(<SourceCard source={source("gmail", { status: "connected" })} />);
  fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Found 4 new items."));
  expect(api).toHaveBeenCalledWith("POST", "/v1/mail-and-drive/google/sync");
  expect(refresh).toHaveBeenCalled();
});

test("Sync now explains a refused or failed sync", async () => {
  api.mockRejectedValue(new ApiError(429, "rate_limited", "The request rate limit was exceeded."));
  render(<SourceCard source={source("outlook", { status: "connected" })} />);
  fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(m.mailDrive.syncTooSoon));

  api.mockReset();
  api.mockResolvedValue({ family: "microsoft", sources: [{ kind: "outlook", label: "Outlook", state: "broken", listed: 0, matched: 0, reason: "token_revoked" }] });
  fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Access was removed in your Microsoft account. Reconnect."));
});

test("Connect sends the browser to the consent screen", async () => {
  const assign = vi.fn();
  vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, assign } as Location);
  api.mockResolvedValue({ authorize_url: "https://accounts.example/consent" });
  render(<SourceCard source={source("google_drive")} />);
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.example/consent"));
  expect(api).toHaveBeenCalledWith("POST", "/v1/mail-and-drive/google_drive/connect");
});

test("the callback banner sentences are plain and complete", () => {
  const b = m.mailDrive.banner;
  for (const text of [b.denied, b.provider, b.scope, b.expired, b.consent]) expect(text).toMatch(/^[A-Z].*\.$/);
});
