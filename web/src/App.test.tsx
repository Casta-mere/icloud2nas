import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import App from "./App";
import { keys } from "./api/queries";
import { calls, db, requireAuth } from "./test/msw";
import { renderWithProviders } from "./test/render";

test("renders the brand once the session is known", async () => {
  renderWithProviders(<App />);
  expect(await screen.findByText("icloud2nas")).toBeInTheDocument();
});

test("no 退出登录 when no admin password is set", async () => {
  renderWithProviders(<App />);
  await screen.findByRole("article", { name: "旭刚" });
  expect(screen.queryByRole("button", { name: "退出登录" })).not.toBeInTheDocument();
});

test("退出登录 deletes the session, drops the cache and shows the login card", async () => {
  requireAuth();
  db.session.authenticated = true;
  const { user, queryClient } = renderWithProviders(<App />);
  await screen.findByRole("article", { name: "旭刚" });
  expect(queryClient.getQueryData(keys.accounts)).toBeDefined();
  // both bars carry it (CSS shows one per width)
  expect(within(screen.getByTestId("phone-bar")).getByRole("button", { name: "退出登录" })).toBeInTheDocument();
  await user.click(within(screen.getByTestId("prompt-bar")).getByRole("button", { name: "退出登录" }));

  expect(await screen.findByLabelText("管理密码")).toBeInTheDocument();
  expect(calls().some((c) => c.method === "DELETE" && c.path === "/api/session")).toBe(true);
  await waitFor(() => expect(queryClient.getQueryData(keys.accounts)).toBeUndefined());
  expect(screen.queryByRole("article", { name: "旭刚" })).not.toBeInTheDocument();
});
