// @vitest-environment jsdom
// The drive chooser of a "From a file" research: a Google Drive | OneDrive switch where only
// the drive connected on the Mail and drive page is enabled (the other says "Connect it on Mail
// and drive" with a link there), the chosen drive's files listed by the server, several files
// ticked and added at once with the drive they came from.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { DriveChooser, enabledSources } from "@/components/ws/DriveChooser";
import type { DriveFile } from "@/lib/gen/api.gen";

const apiMock = vi.fn();
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), api: (...args: unknown[]) => apiMock(...args) }));
afterEach(() => { cleanup(); apiMock.mockReset(); });

const page = (status: string, kind: string | null) => ({
  allowed: true,
  mail: { status: "not_connected", kind: null, source: null, options: [], accounts: 0, items: 0, last_synced_at: null },
  drive: { status, kind, source: null, options: [], accounts: 0, items: 0, last_synced_at: null },
});
const file = (id: string, name: string, type: string): DriveFile => ({
  id, name, type, size_bytes: 2048, modified_at: "2026-10-20T09:00:00Z", google_native: false,
});

function serve(drive: ReturnType<typeof page>, files: Record<string, DriveFile[]>) {
  apiMock.mockImplementation(async (method: string, path: string) => {
    if (method === "GET" && path === "/v1/mail-and-drive") return drive;
    if (method === "GET" && path.startsWith("/v1/documents/drive-files")) {
      const source = new URLSearchParams(path.split("?")[1]).get("source") ?? "google_drive";
      return { files: files[source] ?? [] };
    }
    throw new Error(`unexpected ${method} ${path}`);
  });
}

test("only the connected drive is enabled; the other links to Mail and drive", async () => {
  serve(page("connected", "onedrive"), { onedrive: [file("01ONEDRIVEITEM000001", "Proposal.pdf", "pdf")] });
  render(<DriveChooser onPick={() => undefined} picked={new Set()} />);
  expect(await screen.findByText("Proposal.pdf")).toBeTruthy();
  const onedrive = screen.getByRole("button", { name: "OneDrive" });
  const google = screen.getByRole("button", { name: "Google Drive" }) as HTMLButtonElement;
  expect(onedrive.getAttribute("aria-pressed")).toBe("true");
  expect(google.disabled).toBe(true);
  expect(screen.getByText(/Google Drive is not connected/)).toBeTruthy();
  const link = screen.getByRole("link", { name: /Connect it on Mail and drive/ });
  expect(link.getAttribute("href")).toBe("/mail-and-drive");
  expect(apiMock).toHaveBeenCalledWith("GET", "/v1/documents/drive-files?q=&source=onedrive");
  expect(apiMock.mock.calls.some((c) => c[1] === "/v1/documents/drive-files?q=")).toBe(false); // never Google's
  expect(screen.getByRole("searchbox", { name: "Search your OneDrive by file name" })).toBeTruthy();
  expect(screen.getByText(/^PDF\. .+\. Modified 20 Oct 2026$/)).toBeTruthy();
});

test("several files are ticked and added at once, with their drive", async () => {
  serve(page("connected", "onedrive"), {
    onedrive: [file("01ONEDRIVEITEM000001", "Proposal.pdf", "pdf"), file("01ONEDRIVEITEM000002", "Deck.pptx", "pptx"), file("01ONEDRIVEITEM000003", "Old.docx", "docx")],
  });
  const onPick = vi.fn();
  render(<DriveChooser onPick={onPick} picked={new Set(["01ONEDRIVEITEM000003"])} />);
  const add = await screen.findByRole("button", { name: "Add selected files" });
  expect((add as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("checkbox", { name: "Select Old.docx" }) as HTMLInputElement).disabled).toBe(true); // already added
  expect(screen.getByText("Added")).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select Proposal.pdf" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Select Deck.pptx" }));
  fireEvent.click(screen.getByRole("button", { name: "Add 2 files" }));
  expect(onPick).toHaveBeenCalledTimes(1);
  const [picked, source] = onPick.mock.calls[0]!;
  expect((picked as DriveFile[]).map((f) => f.name)).toEqual(["Proposal.pdf", "Deck.pptx"]);
  expect(source).toBe("onedrive");
  expect(screen.getByRole("button", { name: "Add selected files" })).toBeTruthy(); // selection cleared
});

test("with both drives available the switch moves between them", async () => {
  serve({ ...page("connected", "google_drive") }, {
    google_drive: [file("google-file-0001", "Plan.docx", "docx")],
    onedrive: [file("01ONEDRIVEITEM000001", "Proposal.pdf", "pdf")],
  });
  // the Mail and drive page could not be read: every drive is offered and the server answers
  apiMock.mockImplementationOnce(async () => { throw new Error("offline"); });
  render(<DriveChooser onPick={() => undefined} picked={new Set()} />);
  expect(await screen.findByText("Plan.docx")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "OneDrive" }));
  expect(await screen.findByText("Proposal.pdf")).toBeTruthy();
  expect(screen.queryByText("Plan.docx")).toBeNull();
  expect(screen.getByRole("button", { name: "OneDrive" }).getAttribute("aria-pressed")).toBe("true");
});

test("a drive the server says is not connected links to Mail and drive by name", async () => {
  const { ApiError } = await import("@/lib/api");
  apiMock.mockImplementation(async (_method: string, path: string) => {
    if (path === "/v1/mail-and-drive") return page("broken", "onedrive");
    throw new ApiError(409, "drive_broken", "Reconnect OneDrive on the Mail and drive page.");
  });
  render(<DriveChooser onPick={() => undefined} picked={new Set()} />);
  const link = await screen.findByRole("link", { name: "Connect OneDrive" });
  expect(link.getAttribute("href")).toBe("/mail-and-drive");
  expect(screen.getByText("Reconnect OneDrive on the Mail and drive page.")).toBeTruthy();
  expect(screen.queryByRole("search")).toBeNull();
});

test("no drive connected: both are disabled, each with its link, and nothing is listed", async () => {
  serve(page("not_connected", null), {});
  render(<DriveChooser onPick={() => undefined} picked={new Set()} />);
  await waitFor(() => expect(screen.getAllByRole("link", { name: /Connect it on Mail and drive/ })).toHaveLength(2));
  for (const name of ["Google Drive", "OneDrive"]) {
    expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
  }
  expect(apiMock.mock.calls.some((c) => String(c[1]).startsWith("/v1/documents/drive-files"))).toBe(false);
  expect(screen.queryByRole("search")).toBeNull();
});

test("enabledSources reads the Mail and drive page", () => {
  expect([...enabledSources(page("connected", "google_drive"))]).toEqual(["google_drive"]);
  expect([...enabledSources(page("connected", "sharepoint"))]).toEqual([]);
  expect([...enabledSources(page("not_available", null))]).toEqual([]);
  expect([...enabledSources(null)]).toEqual(["google_drive", "onedrive"]);
  expect([...enabledSources({ document_id: "d1" })]).toEqual(["google_drive", "onedrive"]);
});
