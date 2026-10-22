import { expect, test } from "vitest";
import { db, ids } from "../test/msw";
import { fmtBytes, fmtDateTime, fmtDuration, fmtTime } from "./format";
import { accountName, libraryName } from "./names";

test("times are local and zero-padded", () => {
  const d = new Date(2026, 0, 5, 7, 4);
  expect(fmtTime(d.toISOString())).toBe("07:04");
  expect(fmtDateTime(d.toISOString())).toBe("2026-01-05 07:04");
  expect(fmtTime(null)).toBe("—");
});

test("bytes and durations", () => {
  expect(fmtBytes(512)).toBe("512 B");
  expect(fmtBytes(3.1 * 1024 ** 3)).toBe("3.1 GB");
  expect(fmtDuration("2026-10-21T09:14:00Z", "2026-10-21T09:44:00Z")).toMatch(/^30 /);
  expect(fmtDuration("2026-10-21T09:00:00Z", "2026-10-21T10:05:00Z")).toMatch(/^1 .*05 /);
});

test("names fall back to the id prefix and 已删除", () => {
  expect(libraryName(db.libraries, ids.libXugang)).toBe("旭刚");
  expect(accountName([], "0123456789abcdef")).toMatch(/^01234567 · /);
});
