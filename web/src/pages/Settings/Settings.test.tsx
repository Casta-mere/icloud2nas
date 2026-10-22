import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { calls, db, given } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import App from "../../App";
import { Settings } from "./Settings";

const puts = () => calls().filter((c) => c.method === "PUT" && c.path === "/api/settings");

describe("Settings page", () => {
  test("editing two fields shows 保存 2 项 and one PUT carries both as strings ('1'/'0' for toggles)", async () => {
    const { user } = renderWithProviders(<Settings />);
    const workers = await screen.findByLabelText("并发");
    expect(screen.getByRole("button", { name: "保存 0 项" })).toBeDisabled();
    await user.clear(workers);
    await user.type(workers, "4");
    await user.click(screen.getByRole("switch", { name: "初次全量完成" }));
    const save = screen.getByRole("button", { name: "保存 2 项" });
    await user.click(save);
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]?.body).toEqual({ max_workers: "4", "notify.first_full.enabled": "0" });
    await waitFor(() => expect(screen.getByRole("button", { name: "保存 0 项" })).toBeInTheDocument());
    expect(db.settings.values.max_workers).toBe(4);
  });

  test("放弃 drops the draft", async () => {
    const { user } = renderWithProviders(<Settings />);
    const workers = await screen.findByLabelText("并发");
    await user.clear(workers);
    await user.type(workers, "5");
    await user.click(screen.getByRole("button", { name: "放弃" }));
    expect(screen.getByLabelText("并发")).toHaveValue("2");
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
    await user.click(screen.getByRole("button", { name: "保存 2 项" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("max_workers: must be between 1 and 8");
    expect(workers.closest("div")?.parentElement).toContainElement(alert);
    expect(screen.getByLabelText("每 worker 线程")).toHaveValue("3");
    expect(screen.getByRole("button", { name: "保存 2 项" })).toBeInTheDocument();
    expect(calls().filter((c) => c.method === "GET" && c.path === "/api/settings").length).toBe(before);
  });

  test("发一条测试 shows sent / the 400 detail", async () => {
    const { user } = renderWithProviders(<Settings />);
    await screen.findByLabelText("并发");
    await user.click(screen.getByRole("button", { name: "发一条测试" }));
    expect(await screen.findByText("apprise_urls is empty")).toBeInTheDocument();
    db.settings.values.apprise_urls = "json://nas.lan/hook";
    await user.click(screen.getByRole("button", { name: "发一条测试" }));
    expect(await screen.findByText("发送成功")).toBeInTheDocument();
  });

  test("清除 puts admin_password '' and 设置 puts the typed value then logs in; label reflects admin_password_set", async () => {
    const { user } = renderWithProviders(<Settings />);
    expect(await screen.findByText("未设置 · 任何人可访问")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "清除" })).not.toBeInTheDocument();
    const pw = screen.getByLabelText("管理密码");
    expect(screen.getByRole("button", { name: "设置" })).toBeDisabled();
    await user.type(pw, "s3cret");
    await user.click(screen.getByRole("button", { name: "设置" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]?.body).toEqual({ admin_password: "s3cret" });
    await waitFor(() =>
      expect(calls().some((c) => c.method === "POST" && c.path === "/api/session")).toBe(true),
    );
    expect(calls().find((c) => c.path === "/api/session" && c.method === "POST")?.body).toEqual({ password: "s3cret" });
    expect(await screen.findByText("已设置管理密码")).toBeInTheDocument();
    expect(screen.getByLabelText("管理密码")).toHaveValue("");

    await user.click(screen.getByRole("button", { name: "清除" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "清除" }));
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1]?.body).toEqual({ admin_password: "" });
    expect(await screen.findByText("未设置 · 任何人可访问")).toBeInTheDocument();
  });

  test("设置 in the full app keeps the user on the settings page (login happens inside the mutation)", async () => {
    const { user } = renderWithProviders(<App />);
    const nav = await screen.findByRole("navigation", { name: "主导航" });
    await user.click(within(nav).getByRole("link", { name: "设置" }));
    await user.type(await screen.findByLabelText("管理密码"), "s3cret");
    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(await screen.findByText("已设置管理密码")).toBeInTheDocument();
    expect(db.session).toEqual({ auth_required: true, authenticated: true });
    expect(screen.queryByRole("button", { name: "进入" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存 0 项" })).toBeInTheDocument();
  });

  test("PUT ok but login fails shows the password-already-set message", async () => {
    given("POST", "/api/session", { error: "unauthorized", detail: "wrong password" }, 401);
    const { user } = renderWithProviders(<Settings />);
    await user.type(await screen.findByLabelText("管理密码"), "s3cret");
    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("密码已设置，但自动登录失败，请刷新后登录");
    expect(puts()[0]?.body).toEqual({ admin_password: "s3cret" });
  });
});
