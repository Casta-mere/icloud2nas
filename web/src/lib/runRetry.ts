import type { Run, RunRetry } from "../api/types";

/** The latest in-run retry the worker reported for this run, if any. */
export function runRetry(run: Run): RunRetry | null {
  const retry = run.stats.retry;
  if (typeof retry !== "object" || retry === null) return null;
  const { attempt, of } = retry as Partial<Record<keyof RunRetry, unknown>>;
  return typeof attempt === "number" && typeof of === "number" ? (retry as RunRetry) : null;
}
