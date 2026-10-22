import { act, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import App from "../App";
import { FakeEventSource } from "../events/fakeEventSource";
import { calls } from "../test/msw";
import { renderWithProviders } from "../test/render";

test("prompt bar shows counts, 已断开 · 重连中 when disconnected, pause button toggles via /api/pause", async () => {
  const { user } = renderWithProviders(<App />);
  const bar = () => within(screen.getByTestId("prompt-bar"));

  expect(await screen.findByText("$ icloud2nas status --all · 2 accounts · 1 worker")).toBeInTheDocument();
  expect(bar().getByText("已断开 · 重连中")).toBeInTheDocument();

  act(() => FakeEventSource.latest().emit("hello", { server_time: "2026-10-22T08:00:00+00:00" }));
  expect(bar().getByText("已连接")).toBeInTheDocument();
  act(() => FakeEventSource.latest().fail());
  expect(bar().getByText("已断开 · 重连中")).toBeInTheDocument();

  await user.click(bar().getByRole("button", { name: "全局暂停" }));
  const resume = await bar().findByRole("button", { name: "继续" });
  expect(bar().getByText("已暂停")).toBeInTheDocument();
  expect(within(screen.getByTestId("phone-bar")).getByText("已暂停")).toBeInTheDocument();
  expect(calls().filter((c) => c.path === "/api/pause")).toMatchObject([
    { method: "POST", body: {} },
  ]);

  await user.click(resume);
  expect(await bar().findByRole("button", { name: "全局暂停" })).toBeInTheDocument();
  expect(bar().queryByText("已暂停")).not.toBeInTheDocument();
  expect(calls().filter((c) => c.path === "/api/resume")).toHaveLength(1);
});

test("nav links route between the five pages", async () => {
  const { user } = renderWithProviders(<App />);
  const top = await screen.findByRole("navigation", { name: "主导航" });
  const overview = within(top).getByRole("link", { name: "总览" });
  expect(overview).toHaveClass("border-ink");

  await user.click(within(top).getByRole("link", { name: "设置" }));
  expect(within(top).getByRole("link", { name: "设置" })).toHaveAttribute("aria-current", "page");
  expect(within(top).getByRole("link", { name: "总览" })).not.toHaveAttribute("aria-current");
  expect(await screen.findByText("改动在保存前不会生效")).toBeInTheDocument();
});

test("under 640px the bottom tabs render and the top nav does not", async () => {
  renderWithProviders(<App />, { route: "/accounts" });
  const top = await screen.findByRole("navigation", { name: "主导航" });
  const tabs = screen.getByRole("navigation", { name: "底部导航" });

  // layout is CSS-only: the top nav and the prompt bar hide below `sm`, the tabs show only below it
  expect(top).toHaveClass("max-sm:hidden");
  expect(screen.getByTestId("prompt-bar")).toHaveClass("max-sm:hidden");
  expect(tabs).toHaveClass("sm:hidden");
  expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual([
    "总览",
    "账号",
    "图库",
    "回收站",
    "设置",
  ]);
  expect(within(tabs).getByRole("link", { name: "账号" })).toHaveAttribute("aria-current", "page");
  // the bottom tabs come after the page content, so a code input stays the first interactive element
  const main = screen.getByRole("main");
  expect(main.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
