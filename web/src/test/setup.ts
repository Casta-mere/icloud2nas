import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { FakeEventSource } from "../events/fakeEventSource";
import { resetMsw, server } from "./msw";

vi.stubGlobal("EventSource", FakeEventSource);

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  cleanup();
  resetMsw();
  FakeEventSource.reset();
  localStorage.clear();
  vi.useRealTimers();
});

afterAll(() => {
  server.close();
});
