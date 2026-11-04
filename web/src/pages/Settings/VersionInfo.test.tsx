import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse } from "msw";
import { describe, expect, test, vi } from "vitest";
import App from "../../App";
import type { UpdateInfo } from "../../api/types";
import { calls, db, given } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import { Settings } from "./Settings";
import { UPDATE_COMMAND } from "./VersionInfo";

const COMPARE = "https://github.com/Casta-mere/icloud2nas/compare/v0.0.1...v0.0.2";

const available = (over: Partial<UpdateInfo> = {}): UpdateInfo => ({
  latest: "0.0.2",
  update_available: true,
  checked_at: new Date(Date.now() - 5 * 60_000).toISOString(),
  error: null,
  compare_url: COMPARE,
  ...over,
});

const about = async () => within(await screen.findByRole("region", { name: "关于" }));
const checks = () => calls().filter((c) => c.method === "POST" && c.path === "/api/version/check");
const puts = () => calls().filter((c) => c.method === "PUT" && c.path === "/api/settings");

describe("About card", () => {
  test("shows the running version and no status before any check", async () => {
    renderWithProviders(<Settings />);
    const card = await about();
    expect(await card.findByText("0.0.1")).toBeInTheDocument();
    expect(card.getByRole("button", { name: "检查更新" })).toBeEnabled();
    expect(card.queryByText(/已是最新|新版本|检查失败|正在检查/)).not.toBeInTheDocument();
    expect(card.queryByText(UPDATE_COMMAND)).not.toBeInTheDocument();
    expect(checks()).toHaveLength(0);
  });

  test("检查更新 posts once, shows 正在检查… while pending, then 已是最新", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const result: UpdateInfo = {
      latest: "0.0.1",
      update_available: false,
      checked_at: new Date().toISOString(),
      error: null,
      compare_url: null,
    };
    given("post", "/api/version/check", async () => {
      await gate;
      db.status.update = result; // the server's status carries the result from then on
      return HttpResponse.json(result);
    });
    const { user } = renderWithProviders(<Settings />);
    const card = await about();
    await card.findByText("0.0.1");
    await user.click(card.getByRole("button", { name: "检查更新" }));
    expect(await card.findByText("正在检查…")).toBeInTheDocument();
    expect(card.getByRole("button", { name: "检查更新" })).toBeDisabled();
    release();
    expect(await card.findByText(/^已是最新 · \d+ 秒前检查过$/)).toBeInTheDocument();
    expect(card.queryByText("正在检查…")).not.toBeInTheDocument();
    expect(card.getByRole("button", { name: "检查更新" })).toBeEnabled();
    expect(checks()).toHaveLength(1);
  });

  test("update available: 新版本 0.0.2 可用, the compare link and the command; 复制 copies it", async () => {
    db.status.update = available();
    const { user } = renderWithProviders(<Settings />);
    const card = await about();
    expect(await card.findByText("新版本 0.0.2 可用")).toBeInTheDocument();
    const link = card.getByRole("link", { name: "更新内容" });
    expect(link).toHaveAttribute("href", COMPARE);
    expect(link).toHaveAttribute("target", "_blank");
    expect(UPDATE_COMMAND).toBe("docker compose pull && docker compose up -d");
    expect(card.getByText(UPDATE_COMMAND)).toBeInTheDocument();
    expect(card.queryByText(/已是最新/)).not.toBeInTheDocument();

    await user.click(card.getByRole("button", { name: "复制" }));
    expect(await card.findByText("已复制")).toBeInTheDocument();
    expect(await navigator.clipboard.readText()).toBe(UPDATE_COMMAND);
  });

  test("检查更新 that finds a newer release shows how to update", async () => {
    db.nextCheck = available({ checked_at: new Date().toISOString() });
    const { user } = renderWithProviders(<Settings />);
    const card = await about();
    await card.findByText("0.0.1");
    await user.click(card.getByRole("button", { name: "检查更新" }));
    expect(await card.findByText("新版本 0.0.2 可用")).toBeInTheDocument();
    expect(card.getByText(UPDATE_COMMAND)).toBeInTheDocument();
    expect(card.queryByText(/已是最新|检查失败/)).not.toBeInTheDocument();
  });

  test("no compare link when the server gives none", async () => {
    db.status.update = available({ compare_url: null });
    renderWithProviders(<Settings />);
    const card = await about();
    expect(await card.findByText("新版本 0.0.2 可用")).toBeInTheDocument();
    expect(card.queryByRole("link", { name: "更新内容" })).not.toBeInTheDocument();
  });

  test("复制 without a usable clipboard says so and selects the command", async () => {
    db.status.update = available();
    const { user } = renderWithProviders(<Settings />);
    const card = await about();
    await card.findByText("新版本 0.0.2 可用");
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("not a secure context"));
    await user.click(card.getByRole("button", { name: "复制" }));
    expect(await card.findByText("无法复制，请手动选中命令")).toBeInTheDocument();
    expect(window.getSelection()?.toString()).toBe(UPDATE_COMMAND);
  });

  test("error and update available show together", async () => {
    db.status.update = available({ error: "couldn't reach ghcr.io" });
    renderWithProviders(<Settings />);
    const card = await about();
    expect(await card.findByText("检查失败：couldn't reach ghcr.io")).toBeInTheDocument();
    expect(card.getByText("新版本 0.0.2 可用")).toBeInTheDocument();
    expect(card.getByText(UPDATE_COMMAND)).toBeInTheDocument();
  });

  test("a failed request shows 检查失败 with its detail", async () => {
    given("post", "/api/version/check", { error: "internal", detail: "boom" }, 500);
    const { user } = renderWithProviders(<Settings />);
    const card = await about();
    await card.findByText("0.0.1");
    await user.click(card.getByRole("button", { name: "检查更新" }));
    expect(await card.findByText("检查失败：boom")).toBeInTheDocument();
  });

  test("自动检查更新 saves update_check through 保存", async () => {
    const { user } = renderWithProviders(<Settings />);
    const card = await about();
    const toggle = await card.findByRole("switch", { name: "自动检查更新" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);
    await user.click(screen.getByRole("button", { name: "保存 1 项" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]?.body).toEqual({ update_check: "0" });
    await waitFor(() => expect(db.settings.values.update_check).toBe(false));
    expect(checks()).toHaveLength(0);
  });

  test("/settings#about scrolls the About card into view", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    try {
      renderWithProviders(<App />, { route: "/settings#about" });
      await screen.findByRole("heading", { name: "关于" });
      await waitFor(() => expect(scroll).toHaveBeenCalled());
      expect(scroll.mock.contexts[0]).toHaveAttribute("id", "about");
    } finally {
      delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });
});
