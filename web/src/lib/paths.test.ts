import { expect, test } from "vitest";
import { defaultDir, nests, normalizeRel } from "./paths";

test.each([
  ["小红", "小红"],
  ["  家/小明 ", "家/小明"],
  ["./a//b", "a/b"],
  ["a/b/", "a/b"],
])("normalizeRel(%j) = %j", (input, out) => {
  expect(normalizeRel(input)).toBe(out);
});

test.each(["", "   ", "/abs", "../x", "a/../b", "a/./b", ".hidden", "a/.b", "shared/x", "shared", ".trash", "kept", "kept/x"])(
  "normalizeRel rejects %j",
  (input) => {
    expect(normalizeRel(input)).toBeNull();
  },
);

test("nests: 小明 vs 小明/sub true, 小明 vs 小明2 false", () => {
  expect(nests("小明", "小明/sub")).toBe(true);
  expect(nests("小明/sub", "小明")).toBe(true);
  expect(nests("小明", "小明")).toBe(true);
  expect(nests("小明", "小明2")).toBe(false);
  expect(nests("a/b", "a/c")).toBe(false);
});

test("defaultDir mirrors safe_dir_name", () => {
  expect(defaultDir("  小红 ")).toBe("小红");
  expect(defaultDir("a/b\\c")).toBe("a_b_c");
  expect(defaultDir("..hidden")).toBe("hidden");
  expect(defaultDir("  ")).toBe("");
  expect(defaultDir("...")).toBe("");
});
