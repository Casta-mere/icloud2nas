import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { calls, db, given, ids } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import type { Run } from "../../api/types";
import { Overview } from "./Overview";

const manyRuns = (n: number): Run[] =>
  Array.from({ length: n }, (_, i) => ({
    id: `e${String(i).padStart(31, "0")}`,
    account_id: ids.xugang,
    library_id: ids.libXugang,
    mode: "sync",
    trigger: "schedule",
    status: "ok",
    started_at: new Date(Date.UTC(2026, 9, 21, 12, 0) - i * 60_000).toISOString(),
    ended_at: new Date(Date.UTC(2026, 9, 21, 12, 0) - i * 60_000 + 30_000).toISOString(),
    exit_code: 0,
    stats: { downloaded: i },
    error_kind: null,
    error_message: null,
    plan_file: null,
  }));

describe("Overview", () => {
  test("awaiting_code card renders first and its input is the first focusable element", async () => {
    const { container } = renderWithProviders(<Overview />);
    await screen.findByRole("article", { name: "旭刚" });
    const headings = [...container.querySelectorAll("article")].map((a) => a.getAttribute("aria-label"));
    expect(headings.indexOf("幻伊镜")).toBeLessThan(headings.indexOf("旭刚"));
    const first = container.querySelector("input,button,a[href]");
    expect(first).toBe(screen.getByRole("textbox", { name: "验证码" }));
    expect(first).toHaveAttribute("inputmode", "numeric");
    expect(first).toHaveAttribute("maxlength", "6");
    expect(first).toHaveAttribute("autocomplete", "one-time-code");
  });

  test("every button in the overview has whitespace-nowrap", async () => {
    const { container } = renderWithProviders(<Overview />);
    await screen.findByText("Apple 已向受信设备发送验证码");
    await screen.findAllByText(/同步/);
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBeGreaterThan(2);
    for (const b of buttons) expect(b.className).toContain("whitespace-nowrap");
  });

  test("runs grid shows 20 rows newest first and opens the drawer", async () => {
    db.runs = manyRuns(25);
    const { user } = renderWithProviders(<Overview />);
    const list = await screen.findByRole("list", { name: "最近运行" });
    const rows = await within(list).findAllByRole("listitem");
    expect(rows).toHaveLength(20);
    expect(calls().some((c) => c.path === "/api/runs" && c.search.includes("limit=20"))).toBe(true);
    const times = rows.map((r) => within(r).getByTestId("run-time").textContent);
    expect([...times].sort().reverse()).toEqual(times);
    await user.click(within(rows[0]!).getByRole("button"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/同步 · 定时/)).toBeInTheDocument();
    await waitFor(() => expect(within(dialog).getByText(/start sync/)).toBeInTheDocument());
  });

  test("empty state links to the accounts page", async () => {
    given("GET", "/api/accounts", []);
    renderWithProviders(<Overview />);
    expect(await screen.findByRole("link", { name: "添加账号" })).toHaveAttribute("href", "/accounts");
  });
});
