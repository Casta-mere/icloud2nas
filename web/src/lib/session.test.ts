import { expect, test } from "vitest";
import type { Account } from "../api/types";
import { db } from "../test/msw";
import { sessionExpiry } from "./session";

const NOW = Date.parse("2026-11-04T08:00:00Z");
const HOUR = 60 * 60 * 1000;
const at = (hours: number, estimated = false): Account => ({
  ...db.accounts[0]!,
  session_expires_at: new Date(NOW + hours * HOUR).toISOString(),
  session_expiry_estimated: estimated,
});

test("days left round up, warning from 7 days on", () => {
  expect(sessionExpiry(at(30 * 24), NOW)).toEqual({ key: "session-expires-in", days: 30, warn: false });
  expect(sessionExpiry(at(7 * 24 + 1), NOW)).toEqual({ key: "session-expires-in", days: 8, warn: false });
  expect(sessionExpiry(at(7 * 24), NOW)).toEqual({ key: "session-expires-in", days: 7, warn: true });
  expect(sessionExpiry(at(2), NOW)).toEqual({ key: "session-expires-in-one", days: 1, warn: true });
});

test("estimated expiries read 约 / about", () => {
  expect(sessionExpiry(at(50 * 24, true), NOW)?.key).toBe("session-expires-about");
  expect(sessionExpiry(at(5, true), NOW)?.key).toBe("session-expires-about-one");
});

test("unknown or past expiries", () => {
  expect(sessionExpiry({ ...db.accounts[0]!, session_expires_at: null }, NOW)).toBeNull();
  expect(sessionExpiry({ ...db.accounts[0]!, session_expires_at: "soon" }, NOW)).toBeNull();
  expect(sessionExpiry(at(-1), NOW)).toEqual({ key: "session-expired", days: 0, warn: true });
});
