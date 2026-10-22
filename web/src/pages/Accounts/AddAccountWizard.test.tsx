import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { HttpResponse } from "msw";
import { describe, expect, test, vi } from "vitest";
import { appEvents } from "../../events/useEventStream";
import { calls, db, given } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import { AddAccountWizard } from "./AddAccountWizard";

const NEW_ID = "a3c0ffee000000000000000000000003";
const NEW_RUN = "d5000000000000000000000000000005";

const emit = (topic: string, detail: unknown) =>
  act(() => {
    appEvents.dispatchEvent(new CustomEvent(topic, { detail }));
  });

function createdAccount() {
  given("POST", "/api/accounts", () => {
    db.accounts.push({
      ...db.accounts[1]!,
      id: NEW_ID,
      display_name: "新账号",
      apple_id: "new@icloud.com",
      status: "awaiting_code",
      primary_dir: "新账号",
      library_ids: [],
    });
    return HttpResponse.json({ id: NEW_ID, run_id: NEW_RUN }, { status: 201 });
  });
}

async function fillStep1(user: UserEvent, name: string) {
  await user.type(screen.getByRole("textbox", { name: "显示名" }), name);
}

async function toStep2(user: UserEvent) {
  await fillStep1(user, "新账号");
  await user.click(screen.getByRole("button", { name: /下一步/ }));
}

async function fillStep2AndSubmit(user: UserEvent) {
  await user.type(screen.getByRole("textbox", { name: "Apple ID" }), "new@icloud.com");
  await user.type(screen.getByLabelText("密码"), "pw-secret");
  await user.click(screen.getByRole("button", { name: /下一步/ }));
}

async function toStep4Rendered(user: UserEvent) {
  createdAccount();
  await toStep2(user);
  await fillStep2AndSubmit(user);
  const input = await screen.findByRole("textbox", { name: "验证码" });
  await user.type(input, "123456");
  await waitFor(() => expect(calls().some((c) => c.path.endsWith("/code"))).toBe(true));
  db.accounts.find((a) => a.id === NEW_ID)!.status = "ok";
  emit("run.finished", { run_id: NEW_RUN, status: "ok", mode: "auth", account_id: NEW_ID, exit_code: 0 });
  await screen.findByText("正在发现图库");
}

describe("AddAccountWizard", () => {
  test("step 1 previews /photos/<rel> and blocks 下一步 on a nesting conflict with an existing library", async () => {
    const { user } = renderWithProviders(<AddAccountWizard onDone={vi.fn()} />);
    await screen.findByRole("textbox", { name: "显示名" });
    await fillStep1(user, "旭刚");
    expect(screen.getByText("/photos/旭刚")).toBeInTheDocument();
    expect(await screen.findByText(/和图库「旭刚」/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /下一步/ })).toBeDisabled();

    await user.clear(screen.getByRole("textbox", { name: "显示名" }));
    await user.type(screen.getByRole("textbox", { name: "显示名" }), "新账号");
    expect(screen.getByText("/photos/新账号")).toBeInTheDocument();
    expect(screen.queryByText(/和图库「/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /下一步/ })).toBeEnabled();

    const dir = screen.getByRole("textbox", { name: "照片目录" });
    await user.clear(dir);
    await user.type(dir, "../x");
    expect(screen.getByText(/目录不合法/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /下一步/ })).toBeDisabled();
  });

  test("an IME-confirming Enter in the display name does not advance step 1; a plain Enter does", async () => {
    const { user } = renderWithProviders(<AddAccountWizard onDone={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "显示名" });
    await user.type(input, "新账号");
    expect(fireEvent.keyDown(input, { key: "Enter", keyCode: 229 })).toBe(false);
    expect(screen.getByRole("textbox", { name: "显示名" })).toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("textbox", { name: "Apple ID" })).toBeInTheDocument();
  });

  test("step 2 posts {display_name, apple_id, region, password, target_dir} and 409 lands on apple_id", async () => {
    given("POST", "/api/accounts", { error: "conflict", detail: "apple id exists" }, 409);
    const { user } = renderWithProviders(<AddAccountWizard onDone={vi.fn()} />);
    await screen.findByRole("textbox", { name: "显示名" });
    await user.click(screen.getByRole("radio", { name: /国际/ }));
    await toStep2(user);
    await fillStep2AndSubmit(user);
    const call = await waitFor(() => {
      const c = calls().find((x) => x.method === "POST" && x.path === "/api/accounts");
      expect(c).toBeDefined();
      return c!;
    });
    expect(call.body).toEqual({
      display_name: "新账号",
      apple_id: "new@icloud.com",
      region: "com",
      password: "pw-secret",
      target_dir: "新账号",
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("apple id exists");
    expect(screen.getByRole("textbox", { name: "Apple ID" })).toBeInTheDocument();
  });

  test("step 3 wrong code then right code → step 4 shows 正在发现图库, then the libraries after run.finished mode=list", async () => {
    createdAccount();
    const { user } = renderWithProviders(<AddAccountWizard onDone={vi.fn()} />);
    await toStep2(user);
    await fillStep2AndSubmit(user);
    const input = await screen.findByRole("textbox", { name: "验证码" });
    await user.type(input, "111111");
    await waitFor(() => expect(calls().some((c) => c.path === `/api/accounts/${NEW_ID}/code`)).toBe(true));
    emit("run.event", { run_id: NEW_RUN, event: { ev: "mfa_result", ok: false, error: "Invalid" } });
    expect(await screen.findByText(/验证码不对/)).toHaveTextContent("Invalid");
    const again = screen.getByRole("textbox", { name: "验证码" });
    expect(again).toHaveValue("");
    await user.type(again, "222222");
    await waitFor(() => expect(calls().filter((c) => c.path.endsWith("/code"))).toHaveLength(2));

    db.accounts.find((a) => a.id === NEW_ID)!.status = "ok";
    emit("run.finished", { run_id: NEW_RUN, status: "ok", mode: "auth", account_id: NEW_ID, exit_code: 0 });
    expect(await screen.findByText("正在发现图库")).toBeInTheDocument();

    db.libraries.push({ ...db.libraries[0]!, id: "b3", account_id: NEW_ID, display_name: "新库", target_dir: "新账号" });
    emit("run.finished", { run_id: "d9", status: "ok", mode: "list", account_id: NEW_ID, exit_code: 0 });
    expect(await screen.findByText("新库")).toBeInTheDocument();
    expect(screen.getByText("/photos/新账号")).toBeInTheDocument();
    expect(screen.queryByText("正在发现图库")).not.toBeInTheDocument();
  });

  test("not_activated list run shows the indexing hint and 重试 posts /refresh", async () => {
    const { user } = renderWithProviders(<AddAccountWizard onDone={vi.fn()} />);
    await toStep4Rendered(user);
    db.runs.unshift({
      ...db.runs[0]!,
      id: "d9000000000000000000000000000009",
      account_id: NEW_ID,
      library_id: null,
      mode: "list",
      status: "failed",
      exit_code: 1,
      error_kind: "not_activated",
      error_message: "not activated",
    });
    emit("run.finished", { run_id: "d9000000000000000000000000000009", status: "failed", mode: "list", account_id: NEW_ID, exit_code: 1 });
    expect(await screen.findByText(/几分钟后再试/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() =>
      expect(calls().some((c) => c.method === "POST" && c.path === `/api/accounts/${NEW_ID}/refresh`)).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText(/几分钟后再试/)).not.toBeInTheDocument());
    expect(screen.getByText("正在发现图库")).toBeInTheDocument();
    expect(within(document.body).getByRole("button", { name: "完成" })).toBeInTheDocument();
  });

  test("a list failure other than not_activated shows the error and 重试", async () => {
    const { user } = renderWithProviders(<AddAccountWizard onDone={vi.fn()} />);
    await toStep4Rendered(user);
    db.runs.unshift({
      ...db.runs[0]!,
      id: "d8000000000000000000000000000008",
      account_id: NEW_ID,
      library_id: null,
      mode: "list",
      status: "failed",
      exit_code: 1,
      error_kind: "internal",
      error_message: "boom",
    });
    emit("run.finished", { run_id: "d8000000000000000000000000000008", status: "failed", mode: "list", account_id: NEW_ID, exit_code: 1 });
    expect(await screen.findByText(/发现图库失败：boom/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("正在发现图库")).toBeInTheDocument();
  });

  test("a failed auth run shows the failure and 取消 closes the wizard", async () => {
    createdAccount();
    const onDone = vi.fn();
    const { user } = renderWithProviders(<AddAccountWizard onDone={onDone} />);
    await toStep2(user);
    // while the onboarding login waits for a code, 重新发送 would only 400: it is hidden
    const run = { ...db.runs[0]!, id: NEW_RUN, account_id: NEW_ID, library_id: null, mode: "auth" as const, status: "running" as const };
    db.runs.unshift(run);
    await fillStep2AndSubmit(user);
    await screen.findByRole("textbox", { name: "验证码" });
    await waitFor(() => expect(calls().some((c) => c.path === "/api/runs" && c.search.includes(NEW_ID))).toBe(true));
    await waitFor(() => expect(screen.queryByRole("button", { name: "重新发送" })).not.toBeInTheDocument());
    Object.assign(run, { status: "failed", exit_code: 1, error_kind: "internal", error_message: "network down" });
    emit("run.finished", { run_id: NEW_RUN, status: "failed", mode: "auth", account_id: NEW_ID, exit_code: 1 });
    expect(await screen.findByText(/登录失败：network down/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "重新发送" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(onDone).toHaveBeenCalled();
  });
});
