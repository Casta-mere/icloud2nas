import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { appEvents } from "../../events/useEventStream";
import { calls, db, given, ids } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import { Overview } from "./Overview";

const emit = (run_id: string, event: Record<string, unknown>) =>
  act(() => {
    appEvents.dispatchEvent(new CustomEvent("run.event", { detail: { run_id, event } }));
  });

/** The fixture's live auth run of 幻伊镜 ends (a stale awaiting_code), so 重新发送 is offered. */
const endAuthRun = () => {
  const run = db.runs.find((r) => r.id === ids.runs[3])!;
  run.status = "failed";
  run.ended_at = "2026-10-21T12:10:00+00:00";
};

describe("AccountCard 2FA flow", () => {
  test("entering 6 digits posts /api/accounts/{id}/code with JSON", async () => {
    const { user } = renderWithProviders(<Overview />);
    const input = await screen.findByRole("textbox", { name: "验证码" });
    await user.type(input, "123456");
    await waitFor(() => expect(calls().some((c) => c.path.endsWith("/code"))).toBe(true));
    const call = calls().find((c) => c.path.endsWith("/code"))!;
    expect(call.method).toBe("POST");
    expect(call.path).toBe(`/api/accounts/${ids.huanyijing}/code`);
    expect(call.body).toEqual({ code: "123456" });
    expect(call.headers["content-type"]).toContain("application/json");
  });

  test("mfa_result ok=false shows the reason, clears and refocuses the input, card stays", async () => {
    const { user } = renderWithProviders(<Overview />);
    const input = await screen.findByRole("textbox", { name: "验证码" });
    await screen.findByRole("article", { name: "旭刚" });
    await user.type(input, "111111");
    await waitFor(() => expect(calls().some((c) => c.path.endsWith("/code"))).toBe(true));
    await user.click(document.body);
    emit(ids.runs[3], { ev: "mfa_result", ok: false, error: "Invalid verification code" });
    expect(await screen.findByText(/验证码不对/)).toHaveTextContent("Invalid verification code");
    const again = screen.getByRole("textbox", { name: "验证码" });
    expect(again).toHaveValue("");
    expect(again).toHaveFocus();
    expect(screen.getByText("Apple 已向受信设备发送验证码")).toBeInTheDocument();
  });

  test("events of another account's run are ignored", async () => {
    renderWithProviders(<Overview />);
    await screen.findByRole("textbox", { name: "验证码" });
    emit(ids.runs[2], { ev: "mfa_result", ok: false, error: "nope" });
    expect(screen.queryByText(/验证码不对/)).not.toBeInTheDocument();
  });

  test("needs_reauth → 重新验证 posts /auth, then awaiting_code (via refetch) shows the input", async () => {
    db.accounts[0]!.status = "needs_reauth";
    db.accounts[1]!.status = "ok";
    const { user } = renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "旭刚" });
    expect(within(card).queryByRole("textbox")).not.toBeInTheDocument();
    expect(within(card).getByText("需要验证码")).toBeInTheDocument();
    db.accounts[0]!.status = "awaiting_code";
    await user.click(within(card).getByRole("button", { name: "重新验证" }));
    expect(calls().some((c) => c.method === "POST" && c.path === `/api/accounts/${ids.xugang}/auth`)).toBe(true);
    expect(await within(card).findByRole("textbox", { name: "验证码" })).toBeInTheDocument();
  });

  test("password_rejected links to the accounts page", async () => {
    db.accounts[0]!.status = "password_rejected";
    renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "旭刚" });
    expect(within(card).getByRole("link")).toHaveAttribute("href", "/accounts");
  });

  test("a wrong code on the run started by 重新发送 shows before the runs list knows it", async () => {
    endAuthRun();
    const { user } = renderWithProviders(<Overview />);
    await screen.findByRole("textbox", { name: "验证码" });
    await user.click(screen.getByRole("button", { name: "重新发送" }));
    const newRun = "d7000000000000000000000000000007";
    await waitFor(() => {
      emit(newRun, { ev: "mfa_result", ok: false, error: "Code expired" });
      expect(screen.getByText(/验证码不对/)).toHaveTextContent("Code expired");
    });
    expect(calls().find((c) => c.path.endsWith("/auth"))).toBeDefined();
  });

  test("重新发送 is hidden while the account's auth run is still waiting", async () => {
    renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "幻伊镜" });
    await within(card).findByRole("textbox", { name: "验证码" });
    await waitFor(() => expect(calls().some((c) => c.path === "/api/runs" && c.search.includes(ids.huanyijing))).toBe(true));
    await waitFor(() => expect(within(card).queryByRole("button", { name: "重新发送" })).not.toBeInTheDocument());
    expect(within(card).queryByText("没收到？")).not.toBeInTheDocument();
  });

  test("a 400 'auth session already active' from 重新发送 reads as a hint, not the raw detail", async () => {
    endAuthRun();
    given("POST", "/api/accounts/:id/auth", { error: "invalid", detail: `an auth session is already active for ${ids.huanyijing}` }, 400);
    const { user } = renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "幻伊镜" });
    await user.click(await within(card).findByRole("button", { name: "重新发送" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("这个账号正在等验证码");
    expect(within(card).queryByText(/already active/)).not.toBeInTheDocument();
  });

  test("重新发送 clears the previous /code error", async () => {
    endAuthRun();
    given("POST", "/api/accounts/:id/code", { error: "invalid", detail: "code rejected" }, 400);
    const { user } = renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "幻伊镜" });
    await user.type(await within(card).findByRole("textbox", { name: "验证码" }), "123456");
    expect(await within(card).findByText("code rejected")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "重新发送" }));
    await waitFor(() => expect(within(card).queryByText("code rejected")).not.toBeInTheDocument());
  });

  test("an api error from /code clears and refocuses the input", async () => {
    given("POST", "/api/accounts/:id/code", { error: "invalid", detail: "code rejected" }, 400);
    const { user } = renderWithProviders(<Overview />);
    const input = await screen.findByRole("textbox", { name: "验证码" });
    await user.type(input, "123456");
    expect(await screen.findByText("code rejected")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("textbox", { name: "验证码" })).toHaveValue(""));
    expect(screen.getByRole("textbox", { name: "验证码" })).toHaveFocus();
  });
});
