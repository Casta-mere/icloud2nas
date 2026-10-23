import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { calls, db, given, ids } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import { Trash } from "./Trash";

describe("Trash page", () => {
  test("tree lists every library with trash and selects the newest day by default", async () => {
    renderWithProviders(<Trash />);
    const tree = await screen.findByRole("navigation", { name: "图库 / 日期" });
    const headers = within(tree)
      .getAllByRole("heading")
      .map((h) => h.textContent);
    expect(headers).toEqual(["小明", "小红"]);
    const selected = within(tree)
      .getAllByRole("button")
      .filter((b) => b.hasAttribute("data-selected"));
    expect(selected).toHaveLength(1);
    expect(selected[0]).toHaveTextContent("2026-10-21");
    expect(await screen.findByText("小明 · 2026-10-21")).toBeInTheDocument();
    expect(screen.getByText("3 个文件 · 12.0 MB")).toBeInTheDocument();
    expect(await screen.findByText("2026/10/IMG_0001.HEIC")).toBeInTheDocument();
  });

  test("checking two rows enables 移到保留区 (2) and posts {paths} with library/day in the query", async () => {
    const { user } = renderWithProviders(<Trash />);
    const keep = await screen.findByRole("button", { name: "移到保留区 (0)" });
    expect(keep).toBeDisabled();
    await user.click(await screen.findByRole("checkbox", { name: "2026/10/IMG_0001.HEIC" }));
    await user.click(screen.getByRole("checkbox", { name: "2026/10/IMG_0003.MOV" }));
    const enabled = screen.getByRole("button", { name: "移到保留区 (2)" });
    expect(enabled).toBeEnabled();
    await user.click(enabled);
    await waitFor(() => expect(calls().some((c) => c.method === "POST")).toBe(true));
    const post = calls().find((c) => c.method === "POST");
    expect(post?.path).toBe("/api/trash/2026-10-21/keep");
    expect(decodeURIComponent(post?.search ?? "")).toBe(`?library=${ids.libXugang}`);
    expect(post?.body).toEqual({ paths: ["2026/10/IMG_0001.HEIC", "2026/10/IMG_0003.MOV"] });
    await waitFor(() => expect(screen.getByRole("button", { name: "移到保留区 (0)" })).toBeDisabled());
  });

  test("永久删除整天 asks for confirmation then deletes and moves selection", async () => {
    const { user } = renderWithProviders(<Trash />);
    await user.click(await screen.findByRole("button", { name: "永久删除整天" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(calls().some((c) => c.method === "DELETE")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "永久删除整天" }));
    await waitFor(() => expect(screen.getByText("小明 · 2026-10-19")).toBeInTheDocument());
    const del = calls().find((c) => c.method === "DELETE");
    expect(del?.path).toBe("/api/trash/2026-10-21");
    expect(decodeURIComponent(del?.search ?? "")).toBe(`?library=${ids.libXugang}`);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  test("409 detail renders under the actions and nothing is removed", async () => {
    given("DELETE", "/api/trash/:day", { error: "busy", detail: "library is running" }, 409);
    const { user } = renderWithProviders(<Trash />);
    await user.click(await screen.findByRole("button", { name: "永久删除整天" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "永久删除整天" }));
    expect(await screen.findByText("library is running")).toBeInTheDocument();
    expect(screen.getByText("小明 · 2026-10-21")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
  test("the day's files are requested by library id; the footer points at the library's .icloud2nas/kept", async () => {
    renderWithProviders(<Trash />);
    await screen.findByText("2026/10/IMG_0001.HEIC");
    const get = calls().find((c) => c.method === "GET" && c.path === "/api/trash/2026-10-21");
    expect(decodeURIComponent(get?.search ?? "")).toBe(`?library=${ids.libXugang}`);
    expect(screen.getByText(/保留区在图库文件夹里的 \.icloud2nas\/kept/)).toHaveTextContent("/photos/小明/.icloud2nas/kept");
  });

  test("the prompt line shows the retention, and 不会自动清理 when it is 0", async () => {
    const first = renderWithProviders(<Trash />);
    expect(await screen.findByText(/保留 30 天/)).toBeInTheDocument();
    first.unmount();
    db.settings.values.trash_retention_days = 0;
    renderWithProviders(<Trash />);
    expect(await screen.findByText(/\$ trash · .* · 不会自动清理/)).toBeInTheDocument();
  });
});
