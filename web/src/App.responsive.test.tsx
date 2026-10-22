import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";
import { FakeEventSource } from "./events/fakeEventSource";
import { renderWithProviders } from "./test/render";
import { Drawer } from "./ui/Drawer";

beforeEach(() => {
  // unstubAllGlobals below also drops setup.ts's EventSource stub, so put it back for <App/>
  vi.stubGlobal("EventSource", FakeEventSource);
  // a 375px phone; layout itself is CSS-only, so tests assert the classes that carry it
  vi.stubGlobal("innerWidth", 375);
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({ matches: /max-width/.test(query), media: query, addEventListener() {}, removeEventListener() {} }) as unknown as MediaQueryList,
  );
});
afterEach(() => vi.unstubAllGlobals());

test("at 375px the shell shows BottomTabs and the compact prompt line, not the top nav or prompt bar", async () => {
  const { user } = renderWithProviders(<App />);
  const phone = await screen.findByTestId("phone-bar");
  expect(phone).toHaveClass("sm:hidden");
  expect(screen.getByTestId("prompt-bar")).toHaveClass("max-sm:hidden");
  expect(screen.getByRole("navigation", { name: "主导航" })).toHaveClass("max-sm:hidden");
  expect(screen.getByRole("navigation", { name: "底部导航" })).toHaveClass("sm:hidden");

  // connection chip, pause button and language switch are all in the phone line
  expect(within(phone).getByRole("status")).toBeInTheDocument();
  expect(await within(phone).findByRole("button", { name: "全局暂停" })).toBeEnabled();
  await user.click(within(phone).getByRole("button", { name: "English" }));
  expect(within(phone).getByRole("button", { name: "Pause all" })).toBeInTheDocument();
});

const FOCUSABLE = "input,button,select,textarea,a[href],[tabindex]:not([tabindex='-1'])";

test("at 375px, through the whole shell, the overview is one column with the code input first in <main>", async () => {
  // rule 7 (docs/web.md): the code input is the first control of the page content; only the
  // one-line status strip (connection · pause · language) may come before it
  renderWithProviders(<App />);
  const main = await screen.findByRole("main");
  await within(main).findByRole("article", { name: "旭刚" });
  const code = await within(main).findByRole("textbox", { name: "验证码" });
  const grid = main.querySelector(":scope > .grid, :scope > * > .grid");
  expect(grid).toHaveClass("grid-cols-1");
  expect(grid?.className).not.toMatch(/(^|\s)grid-cols-[2-9]/);
  expect(main.querySelector(FOCUSABLE)).toBe(code);

  // what precedes <main> on a phone is the status strip and the nav that CSS hides there
  const phone = screen.getByTestId("phone-bar");
  expect(phone.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("at 375px the drawer takes the full width", () => {
  renderWithProviders(
    <Drawer open onClose={() => {}} title="x">
      body
    </Drawer>,
  );
  const dialog = screen.getByRole("dialog");
  expect(dialog).toHaveClass("max-sm:w-full");
  expect(dialog).toHaveClass("max-w-full");
});
