import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import App from "../App";
import { ADMIN_PASSWORD, calls, requireAuth } from "../test/msw";
import { renderWithProviders } from "../test/render";

test("wrong password shows detail; right password sets cookie path and shows the shell", async () => {
  requireAuth();
  const { user } = renderWithProviders(<App />);

  expect(await screen.findByText("这台 NAS 设了管理密码")).toBeInTheDocument();
  expect(screen.getByText("icloud2nas-serve --reset-admin-password")).toBeInTheDocument();
  expect(screen.queryByRole("navigation")).not.toBeInTheDocument();

  const password = screen.getByPlaceholderText("管理密码");
  expect(password).toHaveAttribute("type", "password");
  await user.type(password, "nope");
  await user.click(screen.getByRole("button", { name: "进入" }));
  expect(await screen.findByText("wrong password")).toBeInTheDocument();

  await user.clear(password);
  await user.type(password, ADMIN_PASSWORD);
  await user.click(screen.getByRole("button", { name: "进入" }));

  expect((await screen.findAllByRole("link", { name: "总览" })).length).toBeGreaterThan(0);
  expect(screen.queryByText("这台 NAS 设了管理密码")).not.toBeInTheDocument();
  const logins = calls().filter((c) => c.method === "POST" && c.path === "/api/session");
  expect(logins).toHaveLength(2);
  expect(logins[1]).toMatchObject({
    credentials: "same-origin",
    headers: expect.objectContaining({ "content-type": "application/json" }) as unknown,
    body: { password: ADMIN_PASSWORD },
  });
});

test("a 401 from any request drops back to the login card", async () => {
  const { user } = renderWithProviders(<App />);
  expect(await screen.findByText(/2 accounts/)).toBeInTheDocument();
  requireAuth();
  // the next protected request answers 401 → the gate shows the login card (no refetch loop)
  const bar = within(screen.getByTestId("prompt-bar"));
  await bar.findByRole("button", { name: "全局暂停" });
  await user.click(bar.getByRole("button", { name: "全局暂停" }));
  expect(await screen.findByText("这台 NAS 设了管理密码")).toBeInTheDocument();
  const sessionChecks = calls().filter((c) => c.method === "GET" && c.path === "/api/session");
  expect(sessionChecks).toHaveLength(1);
});
