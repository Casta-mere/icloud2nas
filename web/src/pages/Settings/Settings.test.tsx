import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { calls, db, given } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import App from "../../App";
import { Settings } from "./Settings";

const puts = () => calls().filter((c) => c.method === "PUT" && c.path === "/api/settings");
const tests = () => calls().filter((c) => c.method === "POST" && c.path === "/api/notify/test");
/** The floating save bar, named by its own "{n} 项改动未保存" line. */
const saveBar = () => screen.queryByRole("region", { name: /项改动未保存$/ });

describe("Settings page", () => {
  test("editing two fields shows the bar with 2 项改动未保存 and one PUT carries both as strings ('1'/'0' for toggles)", async () => {
    const { user } = renderWithProviders(<Settings />);
    const workers = await screen.findByLabelText("并发");
    expect(saveBar()).not.toBeInTheDocument();
    await user.clear(workers);
    await user.type(workers, "4");
    await user.click(screen.getByRole("switch", { name: "初次全量完成" }));
    const bar = screen.getByRole("region", { name: "2 项改动未保存" });
    await user.click(within(bar).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]?.body).toEqual({ max_workers: "4", "notify.first_full.enabled": "0" });
    await waitFor(() => expect(saveBar()).not.toBeInTheDocument());
    expect(db.settings.values.max_workers).toBe(4);
    expect(screen.getByLabelText("并发")).toHaveValue("4");
  });

  test("放弃 drops the draft and hides the bar", async () => {
    const { user } = renderWithProviders(<Settings />);
    const workers = await screen.findByLabelText("并发");
    await user.clear(workers);
    await user.type(workers, "5");
    await user.click(within(screen.getByRole("region", { name: "1 项改动未保存" })).getByRole("button", { name: "放弃" }));
    expect(screen.getByLabelText("并发")).toHaveValue("2");
    expect(saveBar()).not.toBeInTheDocument();
    expect(puts()).toHaveLength(0);
  });

  test("400 'max_workers: must be between 1 and 8' lands under max_workers; other dirty values survive; nothing refetched", async () => {
    given("PUT", "/api/settings", { error: "invalid", detail: "max_workers: must be between 1 and 8" }, 400);
    const { user } = renderWithProviders(<Settings />);
    const workers = await screen.findByLabelText("并发");
    const threads = screen.getByLabelText("每 worker 线程");
    await user.clear(workers);
    await user.type(workers, "4");
    await user.clear(threads);
    await user.type(threads, "3");
    const before = calls().filter((c) => c.method === "GET" && c.path === "/api/settings").length;
    await user.click(screen.getByRole("button", { name: "保存" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("max_workers: must be between 1 and 8");
    expect(workers.closest("div")?.parentElement).toContainElement(alert);
    expect(screen.getByLabelText("每 worker 线程")).toHaveValue("3");
    expect(screen.getByRole("region", { name: "2 项改动未保存" })).toBeInTheDocument();
    expect(calls().filter((c) => c.method === "GET" && c.path === "/api/settings").length).toBe(before);
  });

  test("切到局域网模式 puts admin_password '' and 设置 puts the typed value then logs in; the hint names the access mode", async () => {
    const { user } = renderWithProviders(<Settings />);
    expect(await screen.findByText("局域网模式 · 无密码，能访问的人都能打开。设置密码即切到外网模式")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "切到局域网模式" })).not.toBeInTheDocument();
    const pw = screen.getByLabelText("管理密码");
    expect(screen.getByRole("button", { name: "设置" })).toBeDisabled();
    await user.type(pw, "s3cret");
    await user.type(screen.getByLabelText("确认密码"), "s3cret");
    await user.click(screen.getByRole("button", { name: "设置" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]?.body).toEqual({ admin_password: "s3cret" });
    await waitFor(() =>
      expect(calls().some((c) => c.method === "POST" && c.path === "/api/session")).toBe(true),
    );
    expect(calls().find((c) => c.path === "/api/session" && c.method === "POST")?.body).toEqual({ password: "s3cret" });
    expect(await screen.findByText("外网模式 · 需要密码")).toBeInTheDocument();
    expect(screen.getByLabelText("管理密码")).toHaveValue("");
    expect(screen.getByLabelText("确认密码")).toHaveValue("");

    await user.click(screen.getByRole("button", { name: "切到局域网模式" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("切换到局域网模式？")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "切到局域网模式" }));
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1]?.body).toEqual({ admin_password: "" });
    expect(await screen.findByText("局域网模式 · 无密码，能访问的人都能打开。设置密码即切到外网模式")).toBeInTheDocument();
  });

  test("设置 in the full app keeps the user on the settings page (login happens inside the mutation)", async () => {
    const { user } = renderWithProviders(<App />);
    const nav = await screen.findByRole("navigation", { name: "主导航" });
    await user.click(within(nav).getByRole("link", { name: "设置" }));
    await user.type(await screen.findByLabelText("管理密码"), "s3cret");
    await user.type(screen.getByLabelText("确认密码"), "s3cret");
    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(await screen.findByText("外网模式 · 需要密码")).toBeInTheDocument();
    expect(db.session).toEqual({ mode: "wan", auth_required: true, authenticated: true, remote: false });
    expect(screen.queryByRole("button", { name: "进入" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发一条测试" })).toBeInTheDocument();
    // the password saves on its own: it never counts as an unsaved change
    expect(saveBar()).not.toBeInTheDocument();
  });

  test("PUT ok but login fails shows the password-already-set message", async () => {
    given("POST", "/api/session", { error: "unauthorized", detail: "wrong password" }, 401);
    const { user } = renderWithProviders(<Settings />);
    await user.type(await screen.findByLabelText("管理密码"), "s3cret");
    await user.type(screen.getByLabelText("确认密码"), "s3cret");
    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("密码已设置，但自动登录失败，请刷新后登录");
    expect(puts()[0]?.body).toEqual({ admin_password: "s3cret" });
  });
  test("设置 stays disabled until 确认密码 matches; a mismatch says so", async () => {
    const { user } = renderWithProviders(<Settings />);
    const set = await screen.findByRole("button", { name: "设置" });
    await user.type(screen.getByLabelText("管理密码"), "s3cret");
    expect(set).toBeDisabled();
    await user.type(screen.getByLabelText("确认密码"), "s3cre");
    expect(set).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("两次输入的密码不一致");
    await user.type(screen.getByLabelText("确认密码"), "t");
    expect(set).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(puts()).toHaveLength(0);
  });
});

describe("发一条测试 tests what is typed", () => {
  test("an edited address is tested, then saved alone: 发送成功，已保存; other edits stay unsaved", async () => {
    const { user } = renderWithProviders(<Settings />);
    const workers = await screen.findByLabelText("并发");
    await user.clear(workers);
    await user.type(workers, "4");
    await user.type(screen.getByLabelText("Apprise 地址"), "json://nas.lan/hook");
    expect(screen.getByRole("region", { name: "2 项改动未保存" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "发一条测试" }));
    expect(await screen.findByText("发送成功，已保存")).toBeInTheDocument();
    expect(tests().map((c) => c.body)).toEqual([{ apprise_urls: "json://nas.lan/hook" }]);
    expect(puts().map((c) => c.body)).toEqual([{ apprise_urls: "json://nas.lan/hook" }]);
    expect(db.settings.values.apprise_urls).toBe("json://nas.lan/hook");
    expect(db.settings.values.max_workers).toBe(2);
    expect(screen.getByLabelText("Apprise 地址")).toHaveValue("json://nas.lan/hook");
    expect(screen.getByLabelText("并发")).toHaveValue("4");
    expect(screen.getByRole("region", { name: "1 项改动未保存" })).toBeInTheDocument();
  });

  test("an unchanged address tests the saved one: 发送成功, nothing saved, no bar", async () => {
    db.settings.values.apprise_urls = "json://saved";
    const { user } = renderWithProviders(<Settings />);
    await screen.findByLabelText("并发");
    await user.click(screen.getByRole("button", { name: "发一条测试" }));
    expect(await screen.findByText("发送成功")).toBeInTheDocument();
    expect(tests().map((c) => c.body)).toEqual([{ apprise_urls: "json://saved" }]);
    expect(puts()).toHaveLength(0);
    expect(saveBar()).not.toBeInTheDocument();
  });

  test("a blank box shows the 400 detail and saves nothing, even with a saved address", async () => {
    db.settings.values.apprise_urls = "json://saved";
    const { user } = renderWithProviders(<Settings />);
    const box = await screen.findByLabelText("Apprise 地址");
    await user.clear(box);
    await user.type(box, " {Enter}");
    await user.click(screen.getByRole("button", { name: "发一条测试" }));
    expect(await screen.findByText("apprise_urls is empty")).toBeInTheDocument();
    expect(tests().map((c) => c.body)).toEqual([{ apprise_urls: " \n" }]);
    expect(puts()).toHaveLength(0);
    expect(db.settings.values.apprise_urls).toBe("json://saved");
    expect(screen.getByRole("region", { name: "1 项改动未保存" })).toBeInTheDocument();
  });

  test("a failed send (sent: false) shows 发送失败, saves nothing and keeps the typed text", async () => {
    given("POST", "/api/notify/test", { sent: false });
    const { user } = renderWithProviders(<Settings />);
    const box = await screen.findByLabelText("Apprise 地址");
    await user.type(box, "json://nas.lan/bad");
    await user.click(screen.getByRole("button", { name: "发一条测试" }));
    expect(await screen.findByText("发送失败")).toBeInTheDocument();
    expect(tests()).toHaveLength(1);
    expect(puts()).toHaveLength(0);
    expect(box).toHaveValue("json://nas.lan/bad");
    expect(screen.getByRole("region", { name: "1 项改动未保存" })).toBeInTheDocument();
  });

  test("a failing save after a good test shows its error by the button and keeps the field unsaved", async () => {
    given("PUT", "/api/settings", { error: "invalid", detail: "disk is read-only" }, 400);
    const { user } = renderWithProviders(<Settings />);
    const box = await screen.findByLabelText("Apprise 地址");
    await user.type(box, "json://nas.lan/hook");
    await user.click(screen.getByRole("button", { name: "发一条测试" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败：disk is read-only");
    expect(screen.getByText("发送成功")).toBeInTheDocument();
    expect(screen.queryByText("发送成功，已保存")).not.toBeInTheDocument();
    expect(puts().map((c) => c.body)).toEqual([{ apprise_urls: "json://nas.lan/hook" }]);
    expect(box).toHaveValue("json://nas.lan/hook");
    expect(screen.getByRole("region", { name: "1 项改动未保存" })).toBeInTheDocument();
  });

  test("in English: Sent and saved", async () => {
    const { user } = renderWithProviders(<Settings />, { lang: "en" });
    await user.type(await screen.findByLabelText("Apprise URLs"), "json://nas.lan/hook");
    await user.click(screen.getByRole("button", { name: "Send a test" }));
    expect(await screen.findByText("Sent and saved")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /unsaved/ })).not.toBeInTheDocument();
  });
});

describe("the floating save bar", () => {
  test("hidden while clean; floats with the live count while dirty; gone again when edits are undone", async () => {
    const { user } = renderWithProviders(<Settings />);
    const workers = await screen.findByLabelText("并发");
    expect(saveBar()).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "放弃" })).not.toBeInTheDocument();
    await user.clear(workers);
    await user.type(workers, "4");
    expect(screen.getByRole("region", { name: "1 项改动未保存" })).toHaveClass("fixed");
    await user.click(screen.getByRole("switch", { name: "初次全量完成" }));
    expect(screen.getByRole("region", { name: "2 项改动未保存" })).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "初次全量完成" }));
    await user.clear(workers);
    await user.type(workers, "2");
    expect(saveBar()).not.toBeInTheDocument();
  });

  test("a client validation error disables 保存 (放弃 still works)", async () => {
    const { user } = renderWithProviders(<Settings />);
    const workers = await screen.findByLabelText("并发");
    await user.clear(workers);
    await user.type(workers, "9");
    const bar = screen.getByRole("region", { name: "1 项改动未保存" });
    expect(within(bar).getByRole("button", { name: "保存" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("超出允许范围");
    await user.click(within(bar).getByRole("button", { name: "放弃" }));
    expect(saveBar()).not.toBeInTheDocument();
    expect(puts()).toHaveLength(0);
  });

  test("a server error for no particular field shows inside the bar", async () => {
    given("PUT", "/api/settings", { error: "invalid", detail: "database is locked" }, 400);
    const { user } = renderWithProviders(<Settings />);
    await user.click(await screen.findByRole("switch", { name: "初次全量完成" }));
    const bar = screen.getByRole("region", { name: "1 项改动未保存" });
    await user.click(within(bar).getByRole("button", { name: "保存" }));
    expect(await within(bar).findByRole("alert")).toHaveTextContent("保存失败：database is locked");
    expect(screen.getByRole("region", { name: "1 项改动未保存" })).toBe(bar);
  });

  test("English pluralises the count", async () => {
    const { user } = renderWithProviders(<Settings />, { lang: "en" });
    await user.click(await screen.findByRole("switch", { name: "First full sync done" }));
    expect(screen.getByRole("region", { name: "1 unsaved change" })).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "Low space" }));
    const bar = screen.getByRole("region", { name: "2 unsaved changes" });
    expect(within(bar).getByRole("button", { name: "Save" })).toBeEnabled();
    expect(within(bar).getByRole("button", { name: "Discard" })).toBeEnabled();
  });
});
