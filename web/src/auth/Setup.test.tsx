import { act, screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import App from "../App";
import { keys } from "../api/queries";
import { calls, db, given, requireSetup } from "../test/msw";
import { renderWithProviders } from "../test/render";

const setupPosts = () => calls().filter((c) => c.method === "POST" && c.path === "/api/setup");

test("the picker replaces the app: no navigation and no data requests", async () => {
  requireSetup();
  renderWithProviders(<App />);

  expect(await screen.findByText("首次设置")).toBeInTheDocument();
  expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "总览" })).not.toBeInTheDocument();
  expect(screen.queryByText("这台 NAS 设了管理密码")).not.toBeInTheDocument();
  expect(calls().filter((c) => c.path === "/api/accounts")).toHaveLength(0);
});

test("LAN mode posts {mode: lan} and opens the app", async () => {
  requireSetup();
  const { user } = renderWithProviders(<App />);

  await user.click(await screen.findByRole("button", { name: "使用局域网模式" }));

  expect((await screen.findAllByRole("link", { name: "总览" })).length).toBeGreaterThan(0);
  expect(setupPosts().map((c) => c.body)).toEqual([{ mode: "lan" }]);
  expect(screen.queryByText("首次设置")).not.toBeInTheDocument();
});

test("WAN mode needs matching passwords, posts them and lands in the shell without a login", async () => {
  requireSetup();
  const { user } = renderWithProviders(<App />);

  const submit = await screen.findByRole("button", { name: "设置密码并进入" });
  const password = screen.getByLabelText("管理密码");
  const again = screen.getByLabelText("确认密码");
  expect(password).toHaveAttribute("type", "password");
  expect(again).toHaveAttribute("type", "password");
  expect(submit).toBeDisabled();

  await user.type(password, "s3cret");
  expect(submit).toBeDisabled();
  await user.type(again, "s3cre");
  expect(submit).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("两次输入的密码不一致");

  await user.type(again, "t");
  expect(submit).toBeEnabled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  await user.click(submit);

  expect((await screen.findAllByRole("link", { name: "总览" })).length).toBeGreaterThan(0);
  expect(setupPosts().map((c) => c.body)).toEqual([{ mode: "wan", password: "s3cret" }]);
  expect(screen.queryByText("这台 NAS 设了管理密码")).not.toBeInTheDocument();
  // the setup response carries the cookie: no separate login
  expect(calls().filter((c) => c.method === "POST" && c.path === "/api/session")).toHaveLength(0);
});

test("a refused choice shows the server's detail and stays on the picker", async () => {
  requireSetup();
  given("POST", "/api/setup", { error: "already set up", detail: "the access mode is changed in Settings" }, 409);
  const { user } = renderWithProviders(<App />);

  await user.click(await screen.findByRole("button", { name: "使用局域网模式" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("the access mode is changed in Settings");
  expect(screen.getByText("首次设置")).toBeInTheDocument();
});

test("outside-note shows above the LAN choice only for a remote request", async () => {
  requireSetup();
  db.session.remote = true;
  const first = renderWithProviders(<App />);
  expect(await screen.findByText("你正从外网访问（反向代理或公网 IP）：局域网模式下任何人都能进来。")).toBeInTheDocument();
  first.unmount();

  db.session.remote = false;
  renderWithProviders(<App />);
  await screen.findByText("首次设置");
  expect(screen.queryByText(/你正从外网访问/)).not.toBeInTheDocument();
});

test("a 428 from any request brings the picker back", async () => {
  const { queryClient } = renderWithProviders(<App />);
  expect(await screen.findByRole("article", { name: "小明" })).toBeInTheDocument();

  db.session.mode = "setup";
  await act(() => queryClient.invalidateQueries({ queryKey: keys.accounts }));

  expect(await screen.findByText("首次设置")).toBeInTheDocument();
  expect(screen.queryByRole("article", { name: "小明" })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("navigation")).not.toBeInTheDocument());
});
