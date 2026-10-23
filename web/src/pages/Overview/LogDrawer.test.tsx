import { act, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { keys } from "../../api/queries";
import { Overview } from "./Overview";
import { calls, db, ids } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import { LogDrawer } from "./LogDrawer";

const logCalls = () => calls().filter((c) => c.path.endsWith("/log")).length;
const runById = (id: string) => db.runs.find((r) => r.id === id)!;

afterEach(() => vi.useRealTimers());

describe("LogDrawer", () => {
  test("drawer refetches the log every 3 s while running and stops when finished", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const running = renderWithProviders(<LogDrawer run={runById(ids.runs[3])} onClose={() => {}} />);
    await screen.findByText(/start auth/);
    expect(logCalls()).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(3200));
    expect(logCalls()).toBe(2);
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(logCalls()).toBe(3);
    running.unmount();

    const before = logCalls();
    renderWithProviders(<LogDrawer run={runById(ids.runs[1])} onClose={() => {}} />);
    await screen.findByText(/start reconcile/);
    await act(() => vi.advanceTimersByTimeAsync(7000));
    expect(logCalls()).toBe(before + 1);
  });

  test("header, stats and actions", async () => {
    const { user } = renderWithProviders(<LogDrawer run={runById(ids.runs[0])} onClose={() => {}} />);
    expect(await screen.findByText(/退出码 0/)).toBeInTheDocument();
    expect(screen.getByText(/同步 · 手动/)).toBeInTheDocument();
    expect(screen.getByText(/下载 3/)).toBeInTheDocument();
    expect(screen.getByText("只显示最后 1 MB")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载 .log" })).toHaveAttribute(
      "href",
      `/api/runs/${ids.runs[0]}/log`,
    );
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await screen.findByText(/start sync/);
    await user.click(screen.getByRole("button", { name: "复制" }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("start sync"));
  });

  test("refetches the log once when the run finishes, then stays quiet", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { user, queryClient } = renderWithProviders(<Overview />);
    const list = await screen.findByRole("list", { name: "最近运行" });
    await user.click(await within(list).findByText(/小红 · 登录/));
    await screen.findByText(/start auth/);
    await act(() => vi.advanceTimersByTimeAsync(3200));
    const polling = logCalls();
    expect(polling).toBe(2);

    const run = runById(ids.runs[3]);
    run.status = "ok";
    run.ended_at = "2026-10-21T12:03:00+00:00";
    run.exit_code = 0;
    await act(() => queryClient.invalidateQueries({ queryKey: keys.runsAll }));
    await act(() => vi.advanceTimersByTimeAsync(100));
    expect(logCalls()).toBe(polling + 1);

    await act(() => vi.advanceTimersByTimeAsync(7000));
    expect(logCalls()).toBe(polling + 1);
  });
});
