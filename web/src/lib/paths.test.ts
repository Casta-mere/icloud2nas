import { expect, test } from "vitest";
import { defaultDir, nests, normalizeRel } from "./paths";

test.each([
  ["幻伊镜", "幻伊镜"],
  ["  家/旭刚 ", "家/旭刚"],
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

test("nests: 旭刚 vs 旭刚/sub true, 旭刚 vs 旭刚2 false", () => {
  expect(nests("旭刚", "旭刚/sub")).toBe(true);
  expect(nests("旭刚/sub", "旭刚")).toBe(true);
  expect(nests("旭刚", "旭刚")).toBe(true);
  expect(nests("旭刚", "旭刚2")).toBe(false);
  expect(nests("a/b", "a/c")).toBe(false);
});

test("defaultDir mirrors safe_dir_name", () => {
  expect(defaultDir("  幻伊镜 ")).toBe("幻伊镜");
  expect(defaultDir("a/b\\c")).toBe("a_b_c");
  expect(defaultDir("..hidden")).toBe("hidden");
  expect(defaultDir("  ")).toBe("");
  expect(defaultDir("...")).toBe("");
});
