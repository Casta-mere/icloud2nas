import { afterEach, expect, test, vi } from "vitest";
import { copyText } from "./clipboard";

afterEach(() => {
  vi.unstubAllGlobals();
  // the tests set it on the instance; drop that to get jsdom's own back
  delete (document as { execCommand?: unknown }).execCommand;
});

test("uses navigator.clipboard when it is there", async () => {
  const writeText = vi.fn(() => Promise.resolve());
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  expect(await copyText("docker compose pull")).toBe(true);
  expect(writeText).toHaveBeenCalledWith("docker compose pull");
});

test("without navigator.clipboard (plain HTTP) it falls back to execCommand on a hidden textarea", async () => {
  vi.stubGlobal("navigator", {});
  let copied: string | undefined;
  document.execCommand = vi.fn(() => {
    copied = (document.activeElement as HTMLTextAreaElement | null)?.value;
    return true;
  });
  expect(await copyText("docker compose up -d")).toBe(true);
  expect(copied).toBe("docker compose up -d");
  expect(document.querySelector("textarea")).toBeNull();
});

test("a rejected clipboard write falls back, and false when nothing works", async () => {
  vi.stubGlobal("navigator", { clipboard: { writeText: () => Promise.reject(new Error("denied")) } });
  document.execCommand = vi.fn(() => {
    throw new Error("unsupported");
  });
  expect(await copyText("x")).toBe(false);
});
