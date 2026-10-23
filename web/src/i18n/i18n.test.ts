import { readdirSync, readFileSync } from "node:fs";
import { afterEach, expect, test, vi } from "vitest";
import { defaultLang, t } from "./index";
import { en } from "./en";
import { zh } from "./zh";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

test("zh and en have identical key sets", () => {
  expect(Object.keys(en).sort()).toEqual(Object.keys(zh).sort());
});

test("t replaces placeholders", () => {
  expect(t("last-login", { time: "小明" })).toContain("小明");
});

test("default language follows navigator.language", () => {
  vi.spyOn(navigator, "language", "get").mockReturnValue("zh-CN");
  expect(defaultLang()).toBe("zh");
  vi.spyOn(navigator, "language", "get").mockReturnValue("en-US");
  expect(defaultLang()).toBe("en");
});

test("a saved language overrides navigator.language", () => {
  vi.spyOn(navigator, "language", "get").mockReturnValue("en-US");
  localStorage.setItem("i2n.lang", "zh");
  expect(defaultLang()).toBe("zh");
});

/**
 * Application sources (no tests, no dictionaries), read from disk on every run: an eager
 * `import.meta.glob` is inlined into this file's cached transform and goes stale.
 */
const SRC = `${process.cwd()}/src/`;
const sources: Record<string, string> = Object.fromEntries(
  readdirSync(SRC, { recursive: true, encoding: "utf8" })
    .filter((rel) => /\.(ts|tsx)$/.test(rel) && !rel.startsWith("i18n/") && !rel.includes(".test."))
    .map((rel) => [rel, readFileSync(`${SRC}${rel}`, "utf8")]),
);

test("every t() key used in src/**/*.tsx exists in zh and en", () => {
  const used = new Set<string>();
  for (const text of Object.values(sources)) {
    for (const m of text.matchAll(/\bt\(\s*"([a-z0-9-]+)"/g)) used.add(m[1] ?? "");
  }
  expect(used.size).toBeGreaterThan(100);
  const missing = [...used].filter((k) => !(k in zh) || !(k in en));
  expect(missing).toEqual([]);
});

/** Prefixes the code builds keys from at runtime (`t(\`mode-${run.mode}\`)` and the like). */
const DYNAMIC_PREFIXES = ["mode-", "opt-", "run-status-", "trigger-"];

test("every key in zh.ts is referenced somewhere in src/ (or built from a dynamic prefix)", () => {
  const code = Object.values(sources).join("\n");
  const unused = Object.keys(zh).filter(
    (k) => !code.includes(`"${k}"`) && !DYNAMIC_PREFIXES.some((p) => k.startsWith(p)),
  );
  expect(unused).toEqual([]);
  // each allowance is still earned: the code really builds keys from it
  for (const prefix of DYNAMIC_PREFIXES) expect(code).toContain(`\`${prefix}\${`);
});
