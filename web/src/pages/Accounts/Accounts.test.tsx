import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { calls, db, given, ids } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import { Accounts } from "./Accounts";

async function openDelete(user: ReturnType<typeof renderWithProviders>["user"]) {
  const row = await screen.findByRole("article", { name: "旭刚" });
  await user.click(within(row).getByRole("button", { name: "更多" }));
  await user.click(within(row).getByRole("button", { name: "删除" }));
  return screen.findByRole("region", { name: /删除账号「旭刚」/ });
}

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
    await waitFor(() => expect(screen.queryByRole("article", { name: "旭刚" })).not.toBeInTheDocument());
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
    const row = await screen.findByRole("article", { name: "旭刚" });
    await user.click(within(row).getByRole("button", { name: "重新验证" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("a run is active for this account");
    expect(within(row).queryByRole("textbox", { name: "验证码" })).not.toBeInTheDocument();
  });

  test("重新验证 is disabled while /auth is pending and opens the code panel once accepted", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    given("POST", "/api/accounts/:id/auth", async () => {
      await gate;
      return HttpResponse.json({ run_id: "d7000000000000000000000000000007" }, { status: 202 });
    });
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "旭刚" });
    const button = within(row).getByRole("button", { name: "重新验证" });
    await user.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    expect(within(row).queryByRole("textbox", { name: "验证码" })).not.toBeInTheDocument();
    await user.click(button);
    release();
    expect(await within(row).findByRole("textbox", { name: "验证码" })).toBeInTheDocument();
    expect(calls().filter((c) => c.path === `/api/accounts/${ids.xugang}/auth`)).toHaveLength(1);
  });

  test("while the account's auth run waits, the row offers 输入验证码 instead of 重新验证", async () => {
    db.accounts[0]!.status = "needs_reauth";
    db.runs.unshift({ ...db.runs[0]!, id: "d8000000000000000000000000000008", account_id: ids.xugang, status: "running" });
    renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "旭刚" });
    expect(await within(row).findByRole("button", { name: "输入验证码" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "重新验证" })).not.toBeInTheDocument();
  });

  test("改密码 patches password and shows the code input", async () => {
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "旭刚" });
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

  test("a blank display name is refused client-side", async () => {
    const { user } = renderWithProviders(<Accounts />);
    const row = await screen.findByRole("article", { name: "旭刚" });
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
    const row = await screen.findByRole("article", { name: "旭刚" });
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
