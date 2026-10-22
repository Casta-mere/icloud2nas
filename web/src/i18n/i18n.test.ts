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
  expect(t("needs-code-title", { name: "旭刚" })).toContain("旭刚");
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

const sources = import.meta.glob<string>("../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
});

test("every t() key used in src/**/*.tsx exists in zh and en", () => {
  const used = new Set<string>();
  for (const [path, text] of Object.entries(sources)) {
    if (path.includes("/i18n/") || path.includes(".test.")) continue;
    for (const m of text.matchAll(/\bt\(\s*"([a-z0-9-]+)"/g)) used.add(m[1] ?? "");
  }
  expect(used.size).toBeGreaterThan(100);
  const missing = [...used].filter((k) => !(k in zh) || !(k in en));
  expect(missing).toEqual([]);
});
