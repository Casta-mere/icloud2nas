import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse } from "msw";
import { describe, expect, test, vi } from "vitest";
import { appEvents } from "../../events/useEventStream";
import { calls, db, given, ids } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import { Accounts } from "./Accounts";

async function openDelete(user: ReturnType<typeof renderWithProviders>["user"]) {
  const row = await screen.findByRole("article", { name: "小明" });
  await user.click(within(row).getByRole("button", { name: "更多" }));
  await user.click(within(row).getByRole("button", { name: "删除" }));
  return screen.findByRole("region", { name: /删除账号「小明」/ });
}

const REAUTH_RUN = "d7000000000000000000000000000007";
const PASSWORD_RUN = "d6000000000000000000000000000006";

const finishAuth = (status: "ok" | "failed", run_id = REAUTH_RUN) => {
  act(() => {
    appEvents.dispatchEvent(
      new CustomEvent("run.finished", {
        detail: {
          run_id,
          status,
          mode: "auth",
          trigger: "reauth",
          account_id: ids.xugang,
          library_id: null,
          exit_code: status === "ok" ? 0 : 1,
          first_full: false,
          account_failures: 0,
        },
      }),
    );
  });
};

describe("Accounts page", () => {
  test("delete dialog sends keep_dir=false only when the trash option is chosen", async () => {
    const { user } = renderWithProviders(<Accounts />);
    const dialog = await openDelete(user);
    expect(within(dialog).getByRole("radio", { name: /保留/ })).toBeChecked();
    await user.click(within(dialog).getByRole("button", { name: "删除账号" }));
    await waitFor(() => expect(calls().some((c) => c.method === "DELETE")).toBe(true));
    expect(calls().find((c) => c.method === "DELETE")).toMatchObject({
      path: `/api/accounts/${ids.xugang}`,
      search: "?keep_dir=true",
    });
    await waitFor(() => expect(screen.queryByRole("article", { name: "小明" })).not.toBeInTheDocument());
  });

  test("trash option deletes with keep_dir=false", async () => {
    const { user } = renderWithProviders(<Accounts />);
    const dialog = await openDelete(user);
    await user.click(within(dialog).getByRole("radio", { name: /回收站/ }));
    await user.click(within(dialog).getByRole("button", { name: "删除账号" }));
    await waitFor(() => expect(calls().some((c) => c.method === "DELETE")).toBe(true));
    expect(calls().find((c) => c.method === "DELETE")?.search).toBe("?keep_dir=false");
  });

  test("the trash option states the configured retention (0 = kept until deleted)", async () => {
    db.settings.values.trash_retention_days = 7;
    const first = renderWithProviders(<Accounts />);
    const dialog = await openDelete(first.user);
    expect(await within(dialog).findByRole("radio", { name: "整个文件夹移到回收站（7 天内可找回）" })).toBeInTheDocument();
    first.unmount();

    db.settings.values.trash_retention_days = 0;
    const second = renderWithProviders(<Accounts />);
    const again = await openDelete(second.user);
    expect(await within(again).findByRole("radio", { name: "整个文件夹移到回收站（不会自动清理）" })).toBeInTheDocument();
  });

  test("重新验证 that fails shows the server's detail and opens no code panel", async () => {
    given("POST", "/api/accounts/:id/auth", { error: "busy", detail: "a run is active for this account" }, 409);
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    await user.click(within(row).getByRole("button", { name: "重新验证" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("a run is active for this account");
    expect(within(row).queryByRole("textbox", { name: "验证码" })).not.toBeInTheDocument();
  });

  test("重新验证 is disabled while /auth is pending and opens the code panel once the account awaits a code", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    given("POST", "/api/accounts/:id/auth", async () => {
      await gate;
      db.accounts[0]!.status = "awaiting_code";
      return HttpResponse.json({ run_id: REAUTH_RUN }, { status: 202 });
    });
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    const button = within(row).getByRole("button", { name: "重新验证" });
    await user.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    expect(within(row).queryByRole("textbox", { name: "验证码" })).not.toBeInTheDocument();
    await user.click(button);
    release();
    expect(await within(row).findByRole("textbox", { name: "验证码" })).toBeInTheDocument();
    expect(calls().filter((c) => c.path === `/api/accounts/${ids.xugang}/auth`)).toHaveLength(1);
  });

  test("重新验证 with a still-valid session never opens the code input and says so for 5 s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    await user.click(within(row).getByRole("button", { name: "重新验证" }));
    await waitFor(() => expect(calls().some((c) => c.path === `/api/accounts/${ids.xugang}/auth`)).toBe(true));
    await waitFor(() => expect(within(row).getByRole("button", { name: "重新验证" })).toBeEnabled());
    expect(within(row).queryByRole("textbox", { name: "验证码" })).not.toBeInTheDocument();
    finishAuth("ok");
    expect(within(row).getByRole("status")).toHaveTextContent("会话仍有效，无需验证码");
    expect(within(row).queryByRole("textbox", { name: "验证码" })).not.toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(within(row).queryByText("会话仍有效，无需验证码")).not.toBeInTheDocument();
  });

  test("a failed re-login shows no session-still-valid notice", async () => {
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    await user.click(within(row).getByRole("button", { name: "重新验证" }));
    await waitFor(() => expect(within(row).getByRole("button", { name: "重新验证" })).toBeEnabled());
    finishAuth("failed");
    expect(within(row).queryByText("会话仍有效，无需验证码")).not.toBeInTheDocument();
  });

  test("the row shows the file owner read-only: name and uid:gid, no way to change it", async () => {
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    expect(within(row).getByText(/Alice \(1000:10\)/)).toBeInTheDocument();
    await waitFor(() => expect(calls().some((c) => c.path === "/api/host-users")).toBe(true));
    await user.click(within(row).getByRole("button", { name: "更多" }));
    expect(within(row).getByRole("button", { name: "改显示名" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "改文件属主" })).not.toBeInTheDocument();
    expect(within(row).queryByRole("combobox", { name: "文件属主" })).not.toBeInTheDocument();
  });

  test("an account without an owner reads root（未设置） on a privileged server", async () => {
    db.accounts[0]!.owner_uid = null;
    db.accounts[0]!.owner_gid = null;
    db.accounts[0]!.owner_name = null;
    renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    expect(await within(row).findByText(/root（未设置）/)).toBeInTheDocument();
    expect(within(row).queryByText(/默认（目录属主）/)).not.toBeInTheDocument();
  });

  test("an owner without a host name shows uid:gid; single-user mode offers no 改文件属主", async () => {
    db.privileged = false;
    db.accounts[0]!.owner_name = null;
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    expect(within(row).getByText(/1000:10/)).toBeInTheDocument();
    await waitFor(() => expect(calls().some((c) => c.path === "/api/host-users")).toBe(true));
    await user.click(within(row).getByRole("button", { name: "更多" }));
    expect(within(row).getByRole("button", { name: "改显示名" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "改文件属主" })).not.toBeInTheDocument();
  });

  test("while the account's auth run waits, the row offers 输入验证码 instead of 重新验证", async () => {
    db.accounts[0]!.status = "needs_reauth";
    db.runs.unshift({ ...db.runs[0]!, id: "d8000000000000000000000000000008", account_id: ids.xugang, status: "running" });
    renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    expect(await within(row).findByRole("button", { name: "输入验证码" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "重新验证" })).not.toBeInTheDocument();
  });

  test("改密码 patches password and opens the code input once the account awaits a code", async () => {
    given("PATCH", "/api/accounts/:id", () => {
      db.accounts[0]!.status = "awaiting_code";
      return HttpResponse.json({ ...db.accounts[0]!, run_id: PASSWORD_RUN });
    });
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    expect(within(row).queryByRole("textbox", { name: "验证码" })).not.toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "改密码" }));
    await user.type(within(row).getByLabelText("新密码"), "new-pass");
    await user.click(within(row).getByRole("button", { name: "确定" }));
    await waitFor(() => expect(calls().some((c) => c.method === "PATCH")).toBe(true));
    expect(calls().find((c) => c.method === "PATCH")).toMatchObject({
      path: `/api/accounts/${ids.xugang}`,
      body: { password: "new-pass" },
    });
    expect(await within(row).findByRole("textbox", { name: "验证码" })).toBeInTheDocument();
  });

  test("改密码 whose login needs no code never renders the code box and says the session is valid", async () => {
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    let flashed = false;
    const watch = new MutationObserver(() => {
      if (row.textContent?.includes("Apple 已向受信设备发送验证码")) flashed = true;
    });
    watch.observe(row, { childList: true, subtree: true, characterData: true });
    await user.click(within(row).getByRole("button", { name: "改密码" }));
    await user.type(within(row).getByLabelText("新密码"), "new-pass");
    await user.click(within(row).getByRole("button", { name: "确定" }));
    await waitFor(() => expect(within(row).queryByLabelText("新密码")).not.toBeInTheDocument());
    finishAuth("ok", PASSWORD_RUN);
    expect(within(row).getByRole("status")).toHaveTextContent("会话仍有效，无需验证码");
    watch.disconnect();
    expect(flashed).toBe(false);
  });

  test("a missed run.finished: the runs list showing the login ok still gives the notice", async () => {
    given("POST", "/api/accounts/:id/auth", () => {
      db.runs.unshift({ ...db.runs[0]!, id: REAUTH_RUN, account_id: ids.xugang, mode: "auth", trigger: "reauth", status: "ok" });
      return HttpResponse.json({ run_id: REAUTH_RUN }, { status: 202 });
    });
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    await user.click(within(row).getByRole("button", { name: "重新验证" }));
    expect(await within(row).findByRole("status")).toHaveTextContent("会话仍有效，无需验证码");
    expect(within(row).queryByRole("textbox", { name: "验证码" })).not.toBeInTheDocument();
  });

  test("a login that went through awaiting_code gets no notice even without a need_mfa event", async () => {
    given("POST", "/api/accounts/:id/auth", () => {
      db.accounts[0]!.status = "awaiting_code";
      return HttpResponse.json({ run_id: REAUTH_RUN }, { status: 202 });
    });
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    await user.click(within(row).getByRole("button", { name: "重新验证" }));
    expect(await within(row).findByRole("textbox", { name: "验证码" })).toBeInTheDocument();
    finishAuth("ok");
    expect(within(row).queryByText("会话仍有效，无需验证码")).not.toBeInTheDocument();
  });

  test("a blank display name is refused client-side", async () => {
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    await user.click(within(row).getByRole("button", { name: "更多" }));
    await user.click(within(row).getByRole("button", { name: "改显示名" }));
    const input = within(row).getByRole("textbox", { name: "显示名" });
    await user.clear(input);
    await user.click(within(row).getByRole("button", { name: "确定" }));
    expect(await within(row).findByRole("alert")).toBeInTheDocument();
    expect(calls().some((c) => c.method === "PATCH")).toBe(false);
  });

  test("an IME-confirming Enter in the rename input sends no PATCH; a plain Enter does", async () => {
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "小明" });
    await user.click(within(row).getByRole("button", { name: "更多" }));
    await user.click(within(row).getByRole("button", { name: "改显示名" }));
    const input = within(row).getByRole("textbox", { name: "显示名" });
    await user.clear(input);
    await user.type(input, "新名字");
    // Safari: isComposing already false, keyCode 229; the capture guard cancels the native submit
    expect(fireEvent.keyDown(input, { key: "Enter", keyCode: 229 })).toBe(false);
    expect(fireEvent.keyDown(input, { key: "Enter", isComposing: true })).toBe(false);
    expect(calls().some((c) => c.method === "PATCH")).toBe(false);
    await user.keyboard("{Enter}");
    await waitFor(() => expect(calls().some((c) => c.method === "PATCH")).toBe(true));
  });
});
