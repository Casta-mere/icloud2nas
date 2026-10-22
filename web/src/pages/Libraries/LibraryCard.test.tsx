import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { FakeEventSource } from "../../events/fakeEventSource";
import { StreamProvider } from "../../events/StreamProvider";
import { calls, db, given, ids } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import type { Run } from "../../api/types";
import { Libraries } from "./Libraries";

function renderPage() {
  return renderWithProviders(
    <StreamProvider>
      <Libraries />
    </StreamProvider>,
  );
}

const card = (name: string) => screen.findByRole("article", { name });
const patches = () => calls().filter((c) => c.method === "PATCH");

describe("LibraryCard", () => {
  test("info rows and form rows share the label column", async () => {
    renderPage();
    const el = await card("旭刚");
    const labels = [...el.querySelectorAll("label")];
    const texts = labels.map((l) => l.textContent);
    for (const want of ["账号", "位置", "文件", "启用", "间隔", "夜间", "目录", "显示名"]) {
      expect(texts).toContain(want);
    }
    for (const l of labels) {
      expect(l.parentElement?.className).toContain("grid-cols-[96px_1fr]");
      expect(l.className).toBe(labels[0]?.className);
    }
  });

  test("间隔 blur patches {interval_min: 15}; 夜间 '3:00' is sent as typed and the normalised '03:00' is shown", async () => {
    const { user } = renderPage();
    const el = await card("旭刚");
    const interval = within(el).getByRole("spinbutton", { name: "间隔" });
    await user.clear(interval);
    await user.type(interval, "15");
    await user.tab();
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toMatchObject({ path: `/api/libraries/${ids.libXugang}`, body: { interval_min: 15 } });

    const nightly = within(el).getByRole("textbox", { name: "夜间" });
    await user.clear(nightly);
    await user.type(nightly, "3:00");
    await user.tab();
    await waitFor(() => expect(patches()).toHaveLength(2));
    expect(patches()[1]?.body).toEqual({ nightly_at: "3:00" });
    await waitFor(() => expect(nightly).toHaveValue("03:00"));
  });

  test("unchanged fields do not patch; Enter commits once", async () => {
    const { user } = renderPage();
    const el = await card("旭刚");
    const name = within(el).getByRole("textbox", { name: "显示名" });
    await user.click(name);
    await user.tab();
    expect(patches()).toHaveLength(0);
    await user.clear(name);
    await user.type(name, "老旭{Enter}");
    await user.tab();
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]?.body).toEqual({ display_name: "老旭" });
  });

  test("a 400 shows the detail under the field and reverts it", async () => {
    const { user } = renderPage();
    const el = await card("旭刚");
    const nightly = within(el).getByRole("textbox", { name: "夜间" });
    await user.clear(nightly);
    await user.type(nightly, "25:99");
    await user.tab();
    expect(await within(el).findByText("nightly_at must be HH:MM")).toBeInTheDocument();
    expect(nightly).toHaveValue("03:00");
  });

  test("启用 toggle patches immediately", async () => {
    const { user } = renderPage();
    const el = await card("旭刚");
    await user.click(within(el).getByRole("switch", { name: "启用" }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]?.body).toEqual({ enabled: false });
  });

  test("目录 change asks for confirmation, then patches; 409 shows the detail under the field and reverts the value", async () => {
    const { user } = renderPage();
    const el = await card("旭刚");
    const dir = within(el).getByRole("textbox", { name: "目录" });
    expect(within(el).getByText("改名会连文件夹一起搬")).toBeInTheDocument();
    await user.clear(dir);
    await user.type(dir, "新目录");
    await user.tab();
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("改目录")).toBeInTheDocument();
    expect(patches()).toHaveLength(0);
    given("PATCH", "/api/libraries/:id", { error: "busy", detail: "a run is active" }, 409);
    await user.click(within(dialog).getByRole("button", { name: "改名" }));
    expect(await within(el).findByText("a run is active")).toBeInTheDocument();
    expect(dir).toHaveValue("旭刚");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  test("目录 confirmed patches only target_dir; cancelling reverts without a request", async () => {
    const { user } = renderPage();
    const el = await card("旭刚");
    const dir = within(el).getByRole("textbox", { name: "目录" });
    await user.clear(dir);
    await user.type(dir, "甲");
    await user.tab();
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "取消" }));
    expect(dir).toHaveValue("旭刚");
    expect(patches()).toHaveLength(0);
    await user.clear(dir);
    await user.type(dir, "乙");
    await user.tab();
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "改名" }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]?.body).toEqual({ target_dir: "乙" });
  });

  test("目录 is locked with a hint while the library runs", async () => {
    const running: Run = { ...db.runs[1]!, id: "f".repeat(32), status: "running", library_id: ids.libXugang };
    db.runs = [running, ...db.runs];
    renderPage();
    const el = await card("旭刚");
    expect(await within(el).findByText("同步进行中，暂不能改目录")).toBeInTheDocument();
    expect(within(el).getByRole("textbox", { name: "目录" })).toBeDisabled();
  });

  test("立即同步 posts /sync with {} and the chip shows 同步中 after run.started", async () => {
    const { user } = renderPage();
    const el = await card("旭刚");
    expect(within(el).queryByText("同步中")).not.toBeInTheDocument();
    await user.click(within(el).getByRole("button", { name: "立即同步" }));
    await waitFor(() => expect(calls().some((c) => c.path.endsWith("/sync"))).toBe(true));
    expect(calls().find((c) => c.path.endsWith("/sync"))).toMatchObject({
      method: "POST",
      path: `/api/libraries/${ids.libXugang}/sync`,
      body: {},
    });
    db.runs = [
      { ...db.runs[1]!, id: "e".repeat(32), status: "running", library_id: ids.libXugang, mode: "sync" },
      ...db.runs,
    ];
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    FakeEventSource.latest().emit("run.started", { run_id: "e".repeat(32), library_id: ids.libXugang });
    expect(await within(el).findByText("同步中")).toBeInTheDocument();
  });

  test("a rejected sync shows the detail under the actions", async () => {
    given("POST", "/api/libraries/:id/reconcile", { error: "busy", detail: "space low" }, 409);
    const { user } = renderPage();
    const el = await card("旭刚");
    await user.click(within(el).getByRole("button", { name: "立即对账" }));
    expect(await within(el).findByText("space low")).toBeInTheDocument();
  });

  test("⋯ menu pauses", async () => {
    const { user } = renderPage();
    const el = await card("旭刚");
    await user.click(within(el).getByRole("button", { name: "更多" }));
    await user.click(within(el).getByRole("button", { name: "暂停" }));
    await waitFor(() => expect(calls().some((c) => c.path.endsWith("/pause"))).toBe(true));
    expect(await within(el).findByText("已暂停")).toBeInTheDocument();
  });

  test("space_paused disables 继续 with its hint", async () => {
    db.libraries[0]!.status = "space_paused";
    const { user } = renderPage();
    const el = await card("旭刚");
    expect(within(el).getByText("空间不足")).toBeInTheDocument();
    await user.click(within(el).getByRole("button", { name: "更多" }));
    expect(within(el).getByRole("button", { name: "继续" })).toBeDisabled();
    expect(within(el).getByText("磁盘空间不足，腾出空间后会自动恢复")).toBeInTheDocument();
  });

  test("only personal libraries are rendered; none shows the empty state with a link", async () => {
    db.libraries = db.libraries.map((l) => ({ ...l, kind: "shared" as const }));
    renderPage();
    expect(await screen.findByText("还没有图库")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "添加账号" })).toHaveAttribute("href", "/accounts");
  });
});
