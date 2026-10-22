import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { onUnauthorized } from "../api/client";
import { calls, given } from "../test/msw";
import { makeQueryClient } from "../test/render";
import { FakeEventSource } from "./fakeEventSource";
import { appEvents, useEventStream } from "./useEventStream";

const unsubscribers: (() => void)[] = [];
afterEach(() => {
  unsubscribers.splice(0).forEach((off) => off());
});

function mount() {
  const queryClient = makeQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const hook = renderHook(() => useEventStream(), { wrapper });
  return { ...hook, queryClient };
}

const sessionChecks = () => calls().filter((c) => c.path === "/api/session").length;

// RTL's waitFor drains through setTimeout, which the backoff test fakes: poll on the real one.
const realSetTimeout = globalThis.setTimeout;
async function until(check: () => void, tries = 200): Promise<void> {
  for (let i = 0; ; i += 1) {
    try {
      check();
      return;
    } catch (e) {
      if (i >= tries) throw e;
      await new Promise((resolve) => realSetTimeout(resolve, 5));
    }
  }
}

test("hello sets connected; library.updated invalidates ['libraries']", () => {
  const { result, queryClient } = mount();
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const es = FakeEventSource.latest();
  expect(es.url).toBe("/api/events");
  expect(result.current.connected).toBe(false);

  act(() => es.emit("hello", { server_time: "2026-10-22T08:00:00+00:00" }));
  expect(result.current.connected).toBe(true);

  act(() => es.emit("library.updated", { library_id: "b1" }));
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["libraries"] });
  expect(invalidate).toHaveBeenCalledTimes(1);

  invalidate.mockClear();
  act(() =>
    es.emit("run.finished", {
      run_id: "r",
      status: "ok",
      library_id: "b1",
      account_id: "a1",
      mode: "sync",
      trigger: "manual",
      exit_code: 0,
      first_full: false,
      account_failures: 0,
    }),
  );
  const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
  expect(keys).toEqual(
    expect.arrayContaining([["runs"], ["status"], ["libraries"], ["accounts"], ["trash"]]),
  );

  invalidate.mockClear();
  act(() => es.emit("account.updated", { account_id: "a1", deleted: true }));
  expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
    ["accounts"],
    ["libraries"],
    ["runs"],
  ]);
});

test("error → session check → reconnect with backoff; expired cookie → unauthorized", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  const timeouts = vi.spyOn(globalThis, "setTimeout");
  const backoffs = () =>
    timeouts.mock.calls.map(([, ms]) => ms ?? 0).filter((ms) => ms >= 1000);
  const unauthorized = vi.fn();
  unsubscribers.push(onUnauthorized(unauthorized));
  const { result, queryClient } = mount();
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");

  const expected = [1000, 2000, 4000, 8000, 16000, 30000, 30000];
  for (const [attempt, delay] of expected.entries()) {
    const es = FakeEventSource.latest();
    timeouts.mockClear();
    act(() => es.fail());
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
    expect(result.current.connected).toBe(false);
    await until(() => expect(sessionChecks()).toBe(attempt + 1));
    await until(() => expect(backoffs()).toEqual([delay]));

    act(() => {
    vi.advanceTimersByTime(delay - 1);
  });
    expect(FakeEventSource.instances).toHaveLength(attempt + 1);
    act(() => {
    vi.advanceTimersByTime(1);
  });
    expect(FakeEventSource.instances).toHaveLength(attempt + 2);
  }

  // hello resets the schedule; the first hello ever does not resync
  act(() => FakeEventSource.latest().emit("hello", { server_time: "" }));
  expect(result.current.connected).toBe(true);
  expect(invalidate).not.toHaveBeenCalled();
  timeouts.mockClear();
  act(() => FakeEventSource.latest().fail());
  await until(() => expect(backoffs()).toEqual([1000]));
  act(() => {
    vi.advanceTimersByTime(1000);
  });
  // a hello after a drop resyncs the whole cache (but the session) exactly once
  act(() => FakeEventSource.latest().emit("hello", { server_time: "" }));
  expect(invalidate).toHaveBeenCalledTimes(1);
  const [filters] = invalidate.mock.calls[0] ?? [];
  expect(filters?.queryKey).toBeUndefined();
  const predicate = filters?.predicate;
  expect(predicate).toBeTypeOf("function");
  queryClient.setQueryData(["session"], { auth_required: false, authenticated: false });
  queryClient.setQueryData(["runs", {}], []);
  const cache = queryClient.getQueryCache();
  const sessionQuery = cache.find({ queryKey: ["session"] });
  const runsQuery = cache.find({ queryKey: ["runs", {}] });
  expect(sessionQuery && predicate?.(sessionQuery)).toBe(false);
  expect(runsQuery && predicate?.(runsQuery)).toBe(true);
  const before = FakeEventSource.instances.length;

  // the cookie expired: the session check says so and the stream stops retrying
  given("GET", "/api/session", { auth_required: true, authenticated: false });
  timeouts.mockClear();
  act(() => FakeEventSource.latest().fail());
  await until(() => expect(unauthorized).toHaveBeenCalledTimes(1));
  act(() => {
    vi.advanceTimersByTime(60_000);
  });
  expect(FakeEventSource.instances).toHaveLength(before);
  expect(backoffs()).toEqual([]);
});

test("run.event is re-dispatched on appEvents", () => {
  mount();
  const seen: unknown[] = [];
  const listener = (e: Event) => seen.push((e as CustomEvent).detail);
  appEvents.addEventListener("run.event", listener);
  act(() => FakeEventSource.latest().emit("run.event", { run_id: "r1", event: { ev: "need_mfa" } }));
  appEvents.removeEventListener("run.event", listener);
  expect(seen).toEqual([{ run_id: "r1", event: { ev: "need_mfa" } }]);
});

test("run.finished is re-dispatched on appEvents", () => {
  mount();
  const seen: unknown[] = [];
  const listener = (e: Event) => seen.push((e as CustomEvent).detail);
  appEvents.addEventListener("run.finished", listener);
  const payload = { run_id: "r1", status: "ok", mode: "list", account_id: "a1", exit_code: 0 };
  act(() => FakeEventSource.latest().emit("run.finished", payload));
  appEvents.removeEventListener("run.finished", listener);
  expect(seen).toEqual([payload]);
});

test("unmount closes the stream", () => {
  const { unmount } = mount();
  const es = FakeEventSource.latest();
  unmount();
  expect(es.readyState).toBe(FakeEventSource.CLOSED);
});
