import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { keys } from "../../api/queries";
import { appEvents } from "../../events/useEventStream";
import { calls, db, given, ids } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import { Overview } from "./Overview";

const emit = (run_id: string, event: Record<string, unknown>) =>
  act(() => {
    appEvents.dispatchEvent(new CustomEvent("run.event", { detail: { run_id, event } }));
  });

/** The fixture's live auth run of 小红 ends (a stale awaiting_code), so 重新发送 is offered. */
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
    await screen.findByRole("article", { name: "小明" });
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
    const card = await screen.findByRole("article", { name: "小明" });
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
    const card = await screen.findByRole("article", { name: "小明" });
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
    const card = await screen.findByRole("article", { name: "小红" });
    await within(card).findByRole("textbox", { name: "验证码" });
    await waitFor(() => expect(calls().some((c) => c.path === "/api/runs" && c.search.includes(ids.huanyijing))).toBe(true));
    await waitFor(() => expect(within(card).queryByRole("button", { name: "重新发送" })).not.toBeInTheDocument());
    expect(within(card).queryByText("没收到？")).not.toBeInTheDocument();
    expect(within(card).getByText("验证码过期后可重新发送")).toBeInTheDocument();
  });

  test("an auth run in needs_mfa (not running) does not hide 重新发送", async () => {
    db.runs.find((r) => r.id === ids.runs[3])!.status = "needs_mfa";
    renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "小红" });
    expect(await within(card).findByRole("button", { name: "重新发送" })).toBeInTheDocument();
    expect(within(card).queryByText("验证码过期后可重新发送")).not.toBeInTheDocument();
  });

  test("重新验证 whose login finishes ok without a code shows 会话仍有效 and no input", async () => {
    db.accounts[0]!.status = "needs_reauth";
    db.accounts[1]!.status = "ok";
    const { user } = renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "小明" });
    await user.click(within(card).getByRole("button", { name: "重新验证" }));
    await waitFor(() => expect(calls().some((c) => c.path === `/api/accounts/${ids.xugang}/auth`)).toBe(true));
    await waitFor(() => expect(within(card).getByRole("button", { name: "重新验证" })).toBeEnabled());
    act(() => {
      appEvents.dispatchEvent(
        new CustomEvent("run.finished", {
          detail: { run_id: "d7000000000000000000000000000007", status: "ok", mode: "auth", account_id: ids.xugang },
        }),
      );
    });
    expect(within(card).getByRole("status")).toHaveTextContent("会话仍有效，无需验证码");
    expect(within(card).queryByRole("textbox", { name: "验证码" })).not.toBeInTheDocument();
  });

  test("a 400 'auth session already active' from 重新发送 reads as a hint, not the raw detail", async () => {
    endAuthRun();
    given("POST", "/api/accounts/:id/auth", { error: "invalid", detail: `an auth session is already active for ${ids.huanyijing}` }, 400);
    const { user } = renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "小红" });
    await user.click(await within(card).findByRole("button", { name: "重新发送" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("这个账号正在等验证码");
    expect(within(card).queryByText(/already active/)).not.toBeInTheDocument();
  });

  test("重新发送 clears the previous /code error", async () => {
    endAuthRun();
    given("POST", "/api/accounts/:id/code", { error: "invalid", detail: "code rejected" }, 400);
    const { user } = renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "小红" });
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

describe("AccountCard session expiry", () => {
  const DAY = 86_400_000;
  /** Half-day offsets keep the rounded-up day count stable while the test runs. */
  const expiresIn = (days: number, estimated = false) => {
    db.accounts[0]!.session_expires_at = new Date(Date.now() + days * DAY).toISOString();
    db.accounts[0]!.session_expiry_estimated = estimated;
  };

  test("会话 N 天后到期 in a neutral tone while more than 7 days are left", async () => {
    expiresIn(29.5);
    renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "小明" });
    expect(await within(card).findByText("会话 30 天后到期")).toHaveAttribute("data-tone", "neutral");
    expect(within(card).getByRole("button", { name: "立即续期" })).toBeEnabled();
  });

  test("warning tone at 7 days or less", async () => {
    expiresIn(4.5);
    renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "小明" });
    expect(await within(card).findByText("会话 5 天后到期")).toHaveAttribute("data-tone", "warn");
  });

  test("an estimated expiry reads 约", async () => {
    expiresIn(49.5, true);
    renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "小明" });
    expect(await within(card).findByText("会话约 50 天后到期")).toBeInTheDocument();
  });

  test("nothing is shown while the expiry is unknown", async () => {
    renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "小明" });
    await within(card).findByText("xugang@example.com");
    expect(within(card).queryByText(/会话.*到期/)).not.toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "立即续期" })).not.toBeInTheDocument();
  });

  test("立即续期 posts /renew, then the code card takes the code", async () => {
    expiresIn(3.5);
    db.accounts[1]!.status = "ok"; // only 小明 asks for a code here
    const { user } = renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "小明" });
    const renew = await within(card).findByRole("button", { name: "立即续期" });
    db.accounts[0]!.status = "awaiting_code"; // what the refetch after /renew sees: Apple asked for a code
    await user.click(renew);
    await waitFor(() =>
      expect(calls().some((c) => c.method === "POST" && c.path === `/api/accounts/${ids.xugang}/renew`)).toBe(true),
    );
    expect(calls().some((c) => c.path.endsWith("/auth"))).toBe(false);
    emit("d8000000000000000000000000000008", { ev: "need_mfa" });
    const input = await within(card).findByRole("textbox", { name: "验证码" });
    expect(within(card).queryByRole("button", { name: "立即续期" })).not.toBeInTheDocument();
    await user.type(input, "654321");
    await waitFor(() => expect(calls().some((c) => c.path === `/api/accounts/${ids.xugang}/code`)).toBe(true));
    expect(calls().find((c) => c.path.endsWith("/code"))!.body).toEqual({ code: "654321" });
  });

  test("a renewal that ends without a code leaves the card in its normal ok state", async () => {
    expiresIn(3.5);
    db.accounts[1]!.status = "ok";
    const { user, queryClient } = renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "小明" });
    const renew = await within(card).findByRole("button", { name: "立即续期" });
    db.accounts[0]!.status = "awaiting_code";
    await user.click(renew);
    await within(card).findByRole("textbox", { name: "验证码" });
    // no code in time: the server puts the account back to ok and fails only the run
    db.accounts[0]!.status = "ok";
    act(() => {
      appEvents.dispatchEvent(
        new CustomEvent("run.finished", {
          detail: { run_id: "d8000000000000000000000000000008", status: "failed", mode: "auth", account_id: ids.xugang },
        }),
      );
    });
    await act(() => queryClient.invalidateQueries({ queryKey: keys.accounts }));
    expect(await within(card).findByRole("button", { name: "立即续期" })).toBeEnabled();
    expect(within(card).getByText("会话 4 天后到期")).toHaveAttribute("data-tone", "warn");
    expect(within(card).getByRole("heading")).toHaveTextContent("正常"); // the account chip
    expect(within(card).queryByRole("textbox", { name: "验证码" })).not.toBeInTheDocument();
    expect(within(card).queryByRole("status")).not.toBeInTheDocument(); // not 会话仍有效
  });

  test("a failed /renew shows its reason on the card", async () => {
    expiresIn(3.5);
    given("POST", "/api/accounts/:id/renew", { error: "busy", detail: "account has an active run or auth session" }, 409);
    const { user } = renderWithProviders(<Overview />);
    const card = await screen.findByRole("article", { name: "小明" });
    await user.click(await within(card).findByRole("button", { name: "立即续期" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("account has an active run or auth session");
  });
});
