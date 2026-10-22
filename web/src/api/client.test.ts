import { HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";
import { calls, given } from "../test/msw";
import { ApiError, onUnauthorized, request } from "./client";

const unsubscribers: (() => void)[] = [];
afterEach(() => {
  unsubscribers.splice(0).forEach((off) => off());
});

test("mutations send application/json and {} when empty", async () => {
  await request("POST", "/api/pause");
  await request("PATCH", "/api/libraries/b1", { enabled: false });
  await request("GET", "/api/status");

  const [pause, patch, status] = calls();
  expect(pause).toMatchObject({ method: "POST", path: "/api/pause", credentials: "same-origin" });
  expect(pause?.headers["content-type"]).toBe("application/json");
  expect(pause?.body).toEqual({});
  expect(patch?.headers["content-type"]).toBe("application/json");
  expect(patch?.body).toEqual({ enabled: false });
  expect(status).toMatchObject({ method: "GET", credentials: "same-origin" });
  expect(status?.headers["content-type"]).toBeUndefined();
});

test("204 resolves to undefined", async () => {
  given("POST", "/api/accounts/:id/code", null, 204);
  await expect(request("POST", "/api/accounts/x/code", { code: "123456" })).resolves.toBeUndefined();
});

test("error body becomes ApiError with status/detail", async () => {
  given("POST", "/api/libraries/:id/sync", { error: "busy", detail: "x" }, 409);
  const failure = request("POST", "/api/libraries/b1/sync");
  await expect(failure).rejects.toBeInstanceOf(ApiError);
  await expect(failure).rejects.toMatchObject({ status: 409, error: "busy", detail: "x" });
});

test("a non-JSON error still becomes an ApiError", async () => {
  given("GET", "/api/status", () => new HttpResponse("upstream down", { status: 502 }));
  await expect(request("GET", "/api/status")).rejects.toMatchObject({
    status: 502,
    detail: "upstream down",
  });
});

test("401 fires onUnauthorized once per response", async () => {
  const cb = vi.fn();
  unsubscribers.push(onUnauthorized(cb));
  given("GET", "/api/accounts", { error: "unauthorized", detail: "login required" }, 401);

  await expect(request("GET", "/api/accounts")).rejects.toMatchObject({ status: 401 });
  expect(cb).toHaveBeenCalledTimes(1);
  await expect(request("GET", "/api/accounts")).rejects.toMatchObject({ status: 401 });
  expect(cb).toHaveBeenCalledTimes(2);

  await request("GET", "/api/status");
  expect(cb).toHaveBeenCalledTimes(2);
});
