import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { calls, given } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import { Trash } from "./Trash";

describe("Trash page", () => {
  test("tree lists libraries first then orphan rels with 已删除, and selects the newest day by default", async () => {
    renderWithProviders(<Trash />);
    const tree = await screen.findByRole("navigation", { name: "图库 / 日期" });
    const headers = within(tree)
      .getAllByRole("heading")
      .map((h) => h.textContent);
    expect(headers).toEqual(["旭刚", "幻伊镜", "old-lib已删除"]);
    const selected = within(tree)
      .getAllByRole("button")
      .filter((b) => b.hasAttribute("data-selected"));
    expect(selected).toHaveLength(1);
    expect(selected[0]).toHaveTextContent("2026-10-21");
    expect(await screen.findByText("旭刚 · 2026-10-21")).toBeInTheDocument();
    expect(screen.getByText("3 个文件 · 12.0 MB")).toBeInTheDocument();
    expect(await screen.findByText("2026/10/IMG_0001.HEIC")).toBeInTheDocument();
  });

  test("checking two rows enables 移到保留区 (2) and posts {paths} with rel/day in the query", async () => {
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
    expect(decodeURIComponent(post?.search ?? "")).toBe("?rel=旭刚");
    expect(post?.body).toEqual({ paths: ["2026/10/IMG_0001.HEIC", "2026/10/IMG_0003.MOV"] });
    await waitFor(() => expect(screen.getByRole("button", { name: "移到保留区 (0)" })).toBeDisabled());
  });

  test("永久删除整天 asks for confirmation then deletes and moves selection", async () => {
    const { user } = renderWithProviders(<Trash />);
    await user.click(await screen.findByRole("button", { name: "永久删除整天" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(calls().some((c) => c.method === "DELETE")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "永久删除整天" }));
    await waitFor(() => expect(screen.getByText("旭刚 · 2026-10-19")).toBeInTheDocument());
    const del = calls().find((c) => c.method === "DELETE");
    expect(del?.path).toBe("/api/trash/2026-10-21");
    expect(decodeURIComponent(del?.search ?? "")).toBe("?rel=旭刚");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  test("409 detail renders under the actions and nothing is removed", async () => {
    given("DELETE", "/api/trash/:day", { error: "busy", detail: "library is running" }, 409);
    const { user } = renderWithProviders(<Trash />);
    await user.click(await screen.findByRole("button", { name: "永久删除整天" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "永久删除整天" }));
    expect(await screen.findByText("library is running")).toBeInTheDocument();
    expect(screen.getByText("旭刚 · 2026-10-21")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});
