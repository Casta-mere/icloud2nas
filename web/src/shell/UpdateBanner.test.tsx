import { act, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import App from "../App";
import type { UpdateInfo } from "../api/types";
import { FakeEventSource } from "../events/fakeEventSource";
import { db, ids } from "../test/msw";
import { renderWithProviders } from "../test/render";
import { DISMISSED_KEY } from "./UpdateBanner";

const COMPARE = "https://github.com/Casta-mere/icloud2nas/compare/v0.0.1...v0.0.2";
const available: UpdateInfo = {
  latest: "0.0.2",
  update_available: true,
  checked_at: "2026-11-04T08:00:00+00:00",
  error: null,
  compare_url: COMPARE,
};

/** 小红 waits for a code in the default fixture, and the banner steps aside for that. */
const noCodeWaiting = () => {
  db.accounts[1]!.status = "ok";
};

/** The shell has loaded both the status and the accounts (the prompt bar counts them). */
const loaded = () => screen.findByText("$ icloud2nas status --all · 2 accounts · 1 worker");

test("no banner while up to date", async () => {
  noCodeWaiting();
  renderWithProviders(<App />);
  await loaded();
  expect(screen.queryByTestId("update-banner")).not.toBeInTheDocument();
});

test("shows 🎉 icloud2nas 0.0.2 发布了！ with 更新内容 and 如何更新", async () => {
  noCodeWaiting();
  db.status.update = available;
  renderWithProviders(<App />);
  const el = await screen.findByTestId("update-banner");
  const banner = within(el);
  expect(banner.getByText("🎉 icloud2nas 0.0.2 发布了！")).toBeInTheDocument();
  expect(banner.getByRole("link", { name: "更新内容" })).toHaveAttribute("href", COMPARE);
  expect(banner.getByRole("link", { name: "更新内容" })).toHaveAttribute("target", "_blank");
  expect(banner.getByRole("link", { name: "如何更新" })).toHaveAttribute("href", "/settings#about");
  expect(banner.getByRole("button", { name: "关闭提示" })).toBeInTheDocument();

  // phones get one short line instead (layout is CSS-only, so assert the classes that carry it)
  expect(banner.getByText("🎉 icloud2nas 0.0.2 发布了！").closest(".max-sm\\:hidden")).not.toBeNull();
  const short = banner.getByText("🎉 0.0.2 发布了");
  expect(short.closest(".sm\\:hidden")).not.toBeNull();
  expect(banner.getByRole("link", { name: "更新" })).toHaveAttribute("href", "/settings#about");
  for (const link of banner.getAllByRole("link")) expect(link).toHaveClass("whitespace-nowrap");

  // full-bleed, above the header and the page content
  expect(el.compareDocumentPosition(screen.getByRole("banner")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(el.compareDocumentPosition(screen.getByRole("main")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("关闭提示 hides it and remembers the version", async () => {
  noCodeWaiting();
  db.status.update = available;
  const first = renderWithProviders(<App />);
  const banner = await screen.findByTestId("update-banner");
  await first.user.click(within(banner).getByRole("button", { name: "关闭提示" }));
  expect(screen.queryByTestId("update-banner")).not.toBeInTheDocument();
  expect(localStorage.getItem(DISMISSED_KEY)).toBe("0.0.2");
  first.unmount();

  // a reload: still dismissed for 0.0.2
  renderWithProviders(<App />);
  await loaded();
  expect(screen.queryByTestId("update-banner")).not.toBeInTheDocument();
});

test("a newer version brings it back", async () => {
  noCodeWaiting();
  localStorage.setItem(DISMISSED_KEY, "0.0.2");
  db.status.update = { ...available, latest: "0.0.3", compare_url: COMPARE.replace("0.0.2", "0.0.3") };
  renderWithProviders(<App />);
  expect(await screen.findByText("🎉 icloud2nas 0.0.3 发布了！")).toBeInTheDocument();
});

test("hidden while an account waits for a code, back once none does", async () => {
  db.status.update = available;
  renderWithProviders(<App />);
  await loaded();
  await screen.findByRole("textbox", { name: "验证码" });
  expect(screen.queryByTestId("update-banner")).not.toBeInTheDocument();

  db.accounts[1]!.status = "ok";
  act(() => FakeEventSource.latest().emit("account.status", { account_id: ids.huanyijing, status: "ok" }));
  expect(await screen.findByTestId("update-banner")).toBeInTheDocument();
});

test("a version.checked event shows it without a reload", async () => {
  noCodeWaiting();
  renderWithProviders(<App />);
  await loaded();
  expect(screen.queryByTestId("update-banner")).not.toBeInTheDocument();

  db.status.update = available;
  act(() => FakeEventSource.latest().emit("version.checked", {}));
  expect(await screen.findByTestId("update-banner")).toBeInTheDocument();
});

test("如何更新 opens Settings at the About card", async () => {
  noCodeWaiting();
  db.status.update = available;
  const scroll = vi.fn();
  Element.prototype.scrollIntoView = scroll;
  try {
    const { user } = renderWithProviders(<App />);
    const banner = await screen.findByTestId("update-banner");
    await user.click(within(banner).getByRole("link", { name: "如何更新" }));
    expect(await screen.findByRole("heading", { name: "关于" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "主导航" });
    expect(within(nav).getByRole("link", { name: "设置" })).toHaveAttribute("aria-current", "page");
    await waitFor(() => expect(scroll).toHaveBeenCalled());
    expect(scroll.mock.contexts.at(-1)).toHaveAttribute("id", "about");
  } finally {
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  }
});
