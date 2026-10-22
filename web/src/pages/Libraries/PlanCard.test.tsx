import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { FakeEventSource } from "../../events/fakeEventSource";
import { StreamProvider } from "../../events/StreamProvider";
import { calls, db, given, ids } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import type { PlanDetail } from "../../api/types";
import { Libraries } from "./Libraries";

function renderPage() {
  return renderWithProviders(
    <StreamProvider>
      <Libraries />
    </StreamProvider>,
  );
}

const resolve = async (status: "executed" | "pending") => {
  const plan = db.plans[0]!;
  plan.status = status;
  await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
  FakeEventSource.latest().emit("plan.resolved", { plan_id: plan.id, library_id: plan.library_id, status });
};

describe("PlanCard", () => {
  test("pending plan renders paths and total; 放行 posts approve and disables until plan.resolved executed removes the card", async () => {
    const detail: PlanDetail = {
      ...db.plans[0]!,
      paths: ["2024/05/IMG_0421.HEIC", "2024/05/IMG_0422.HEIC", "2024/06/IMG_0510.JPG"],
      total: 612,
    };
    given("GET", "/api/plans/:id", detail);
    const { user } = renderPage();
    const el = await screen.findByRole("article", { name: "幻伊镜" });
    expect(await within(el).findByText("对账待确认")).toBeInTheDocument();
    expect(await within(el).findByText("612")).toBeInTheDocument();
    const list = await within(el).findByRole("list");
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("2024/05/IMG_0421.HEIC");
    expect(list.className).toContain("max-h-[120px]");
    expect(list.className).toContain("overflow-auto");
    expect(within(el).getByText("… 还有 609 个")).toBeInTheDocument();
    expect(within(el).getByText(/计划生成于 \d\d:\d\d/)).toBeInTheDocument();

    await user.click(within(el).getByRole("button", { name: "放行 612 个" }));
    await waitFor(() => expect(calls().some((c) => c.path.endsWith("/approve"))).toBe(true));
    expect(calls().find((c) => c.path.endsWith("/approve"))).toMatchObject({
      method: "POST",
      path: `/api/plans/${ids.plan}/approve`,
      body: {},
    });
    const busy = await within(el).findByRole("button", { name: "执行中…" });
    expect(busy).toBeDisabled();

    await resolve("executed");
    await waitFor(() => expect(within(el).queryByText("执行中…")).not.toBeInTheDocument());
    expect(within(el).queryByRole("list")).not.toBeInTheDocument();
    expect(within(el).getByText("正常")).toBeInTheDocument();
  });

  test("plan.resolved pending shows apply-failed and re-enables 放行", async () => {
    const { user } = renderPage();
    const el = await screen.findByRole("article", { name: "幻伊镜" });
    await user.click(await within(el).findByRole("button", { name: "放行 612 个" }));
    expect(await within(el).findByRole("button", { name: "执行中…" })).toBeDisabled();
    expect(within(el).queryByText(/执行失败/)).not.toBeInTheDocument();

    await resolve("pending");
    expect(await within(el).findByText(/执行失败/)).toBeInTheDocument();
    expect(within(el).getByRole("button", { name: "放行 612 个" })).toBeEnabled();
  });

  test("拒绝 posts reject and the card goes away", async () => {
    const { user } = renderPage();
    const el = await screen.findByRole("article", { name: "幻伊镜" });
    await user.click(await within(el).findByRole("button", { name: "拒绝" }));
    await waitFor(() => expect(calls().some((c) => c.path.endsWith("/reject"))).toBe(true));
    await waitFor(() => expect(within(el).queryByRole("button", { name: "拒绝" })).not.toBeInTheDocument());
  });
});
