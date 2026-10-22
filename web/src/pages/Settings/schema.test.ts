import { describe, expect, test } from "vitest";
import { FIELDS, fromValues, toWire, validate } from "./schema";
import { db } from "../../test/msw";

const API_KEYS = [
  "apprise_urls", "public_url",
  "notify.needs_code.enabled", "notify.password_rejected.enabled", "notify.repeated_failure.enabled",
  "notify.plan_pending.enabled", "notify.space_low.enabled", "notify.first_full.enabled",
  "max_workers", "threads_per_worker", "min_free_gb", "file_match_policy", "folder_structure",
  "live_photo_mov_filename_policy", "keep_unicode_in_filenames",
  "trash_retention_days", "log_retention_runs", "admin_password",
];

describe("settings schema", () => {
  test("FIELDS cover exactly the keys in docs/api.md (no paused, no internal keys) and each notify event once", () => {
    const keys = FIELDS.map((f) => f.key);
    expect(keys).toEqual(API_KEYS);
    expect(keys).not.toContain("paused");
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys.filter((k) => k.startsWith("notify.")).length).toBe(6);
  });

  test("toWire, validate and fromValues", () => {
    const f = (k: string) => FIELDS.find((x) => x.key === k)!;
    expect(toWire(true, f("keep_unicode_in_filenames"))).toBe("1");
    expect(toWire(false, f("keep_unicode_in_filenames"))).toBe("0");
    expect(toWire(3, f("max_workers"))).toBe("3");
    expect(validate(f("max_workers"), "9")).not.toBeNull();
    expect(validate(f("max_workers"), "8")).toBeNull();
    expect(validate(f("min_free_gb"), "0.5")).toBeNull();
    expect(validate(f("log_retention_runs"), "0")).not.toBeNull();
    expect(validate(f("file_match_policy"), "bogus")).not.toBeNull();
    expect(validate(f("public_url"), "")).toBeNull();
    const wire = fromValues(db.settings.values);
    expect(wire.max_workers).toBe("2");
    expect(wire["notify.needs_code.enabled"]).toBe("1");
    expect(Object.keys(wire)).toHaveLength(17);
  });
});
