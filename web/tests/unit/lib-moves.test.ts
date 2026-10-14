// Rules that used to sit inside components: the sidebar notes, the Accounts order, limit wording.
import { expect, test } from "vitest";
import { ApiError } from "@/lib/api";
import { orderAccounts, subline, type AccountItem } from "@/lib/accounts-list";
import { limitLine, limitReached } from "@/lib/limits";
import { shellNotes } from "@/lib/notifications";
import type { MyLimits, Notifications } from "@/lib/gen/api.gen";

function note(over: Record<string, unknown>) {
  return { id: "n1", kind: "research_finished", read: false, account_name: "Acme", account_domain: "acme.example", scope: null, reason: null, ...over };
}
const wrap = (...items: ReturnType<typeof note>[]) => ({ notifications: items }) as unknown as Notifications;

test("only unread notes show, at most three, newest first as given", () => {
  const notes = shellNotes(wrap(note({ id: "a" }), note({ id: "b", read: true }), note({ id: "c" }), note({ id: "d" }), note({ id: "e" })));
  expect(notes.map((n) => n.id)).toEqual(["a", "c", "d"]);
  expect(shellNotes(null)).toEqual([]);
});

test("a note says what happened, where it leads and whether it is a warning", () => {
  const [failed] = shellNotes(wrap(note({ kind: "research_failed", reason: "The source was unreachable." })));
  expect(failed).toMatchObject({ text: "Acme: research did not finish", meta: "The source was unreachable.", href: "/accounts/acme.example", warn: true });
  const [expiring] = shellNotes(wrap(note({ kind: "access_expiring" })));
  expect(expiring).toMatchObject({ href: "/", warn: true });
  const [raised] = shellNotes(wrap(note({ kind: "limit_request_approved" })));
  expect(raised).toMatchObject({ href: "/research/new", warn: false });
  const [refresh] = shellNotes(wrap(note({ kind: "refresh_finished", scope: "jobs", account_domain: null })));
  expect(refresh).toMatchObject({ text: "Acme: refresh finished (job postings)", href: "/accounts" });
  const [encoded] = shellNotes(wrap(note({ account_domain: "a b/c" })));
  expect(encoded!.href).toBe("/accounts/a%20b%2Fc");
});

function account(over: Partial<AccountItem>): AccountItem {
  return { domain: "a.example", name: "A", state: "no_change", new_signals: 0, last_researched_at: null, ...over } as AccountItem;
}

test("accounts with news come first, then the most recently researched, then by name", () => {
  const order = orderAccounts([
    account({ domain: "z.example", name: "Zed", last_researched_at: "2026-10-01T00:00:00Z" }),
    account({ domain: "n.example", name: "News", state: "new_signals", new_signals: 2, last_researched_at: "2026-09-01T00:00:00Z" }),
    account({ domain: "r.example", name: "Recent", last_researched_at: "2026-10-05T00:00:00Z" }),
    account({ domain: "b.example", name: "Bee", last_researched_at: "2026-10-01T00:00:00Z" }),
  ]).map((a) => a.name);
  expect(order).toEqual(["News", "Recent", "Bee", "Zed"]);
  expect(subline(account({ state: "new_signals", new_signals: 1 }))).toEqual({ text: "1 new signal", tone: "ok" });
  expect(subline(account({ state: "something_else" as AccountItem["state"] })).text).toBe("Not researched yet");
});

test("limits read in plain words", () => {
  const limits = { reset: "monthly", researches: { left: 1, limit: 2 }, chats: { left: 3, limit: 1 } } as unknown as MyLimits;
  expect(limitLine(limits, "researches")).toBe("1 of 2 researches left this month");
  expect(limitLine({ ...limits, reset: "never" } as MyLimits, "chats")).toBe("3 of 1 chat left");
  expect(limitReached(new ApiError(429, "limit_reached", "No chats left.", { limit: "chats" }))).toEqual({ kind: "chats", message: "No chats left." });
  expect(limitReached(new ApiError(500, "other", ""))).toBeNull();
  expect(limitReached(new Error("x"))).toBeNull();
});
