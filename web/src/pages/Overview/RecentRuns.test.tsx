import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Run } from "../../api/types";
import { renderWithProviders } from "../../test/render";
import { RecentRuns } from "./RecentRuns";

const run = (over: Partial<Run>): Run => ({
  id: "r1",
  account_id: "a1",
  library_id: null,
  mode: "sync",
  trigger: "schedule",
  status: "running",
  started_at: "2026-11-04T08:00:00+00:00",
  ended_at: null,
  exit_code: null,
  stats: {},
  error_kind: null,
  error_message: null,
  plan_file: null,
  ...over,
});

const retry = { attempt: 1, of: 3, wait_s: 10, kind: "network", message: "stall" };
const list = (runs: Run[]) => <RecentRuns runs={runs} libraries={[]} accounts={[]} onOpen={() => {}} />;

describe("RecentRuns", () => {
  test("a running run retrying an Apple stall shows the note and stays running", () => {
    renderWithProviders(list([run({ stats: { retry } })]));
    expect(screen.getByText(/Apple 无响应，重试 1\/3/)).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
  });

  test("the note in English", () => {
    renderWithProviders(list([run({ stats: { retry } })]), { lang: "en" });
    expect(screen.getByText(/Apple not responding, retry 1\/3/)).toBeInTheDocument();
  });

  test("no note without a retry, or once the run has ended", () => {
    const ended = run({
      id: "r2",
      status: "ok",
      ended_at: "2026-11-04T08:05:00+00:00",
      exit_code: 0,
      stats: { retry },
    });
    renderWithProviders(list([run({}), ended]));
    expect(screen.queryByText(/Apple/)).not.toBeInTheDocument();
  });
});
