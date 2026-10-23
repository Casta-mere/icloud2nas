import { screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, test } from "vitest";
import { given, server } from "../test/msw";
import { renderWithProviders } from "../test/render";
import { Accounts } from "./Accounts/Accounts";
import { Libraries } from "./Libraries/Libraries";
import { Overview } from "./Overview/Overview";
import { Settings } from "./Settings/Settings";
import { Trash } from "./Trash/Trash";

const PAGES: [string, () => ReactElement, string, string][] = [
  ["Overview", () => <Overview />, "/api/accounts", "小明"],
  ["Accounts", () => <Accounts />, "/api/accounts", "小明"],
  ["Libraries", () => <Libraries />, "/api/libraries", "小明"],
  ["Trash", () => <Trash />, "/api/trash", "小明 · 2026-10-21"],
  ["Settings", () => <Settings />, "/api/settings", "通知"],
];

describe("page loading and error states", () => {
  test.each(PAGES)("%s shows 加载中… while its data is pending", async (_name, page, path) => {
    given("GET", path, () => new Promise<never>(() => undefined));
    renderWithProviders(page());
    expect(await screen.findByRole("status")).toHaveTextContent("加载中…");
  });

  test.each(PAGES)("%s shows the error detail and 重试 refetches", async (_name, page, path, ready) => {
    given("GET", path, { error: "internal", detail: "database is locked" }, 500);
    const { user } = renderWithProviders(page());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("database is locked");
    server.resetHandlers();
    await user.click(within(alert).getByRole("button", { name: "重试" }));
    expect((await screen.findAllByText(ready)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/database is locked/)).not.toBeInTheDocument();
  });
});
