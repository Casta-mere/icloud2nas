import { fireEvent, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../test/render";
import { CodeInput } from "./CodeInput";

// Chrome/Firefox: isComposing is set. Safari: it is already cleared but keyCode is 229.
const composing = [
  ["isComposing", { isComposing: true }],
  ["keyCode 229", { keyCode: 229 }],
] as const;

test.each(composing)("an Enter that confirms an IME composition (%s) does not submit the code", (_n, init) => {
  const onSubmit = vi.fn();
  renderWithProviders(<CodeInput onSubmit={onSubmit} pending={false} />);
  const input = screen.getByRole("textbox", { name: "验证码" });
  // the sixth digit auto-submits; clear that call so only the Enter path is under test
  fireEvent.change(input, { target: { value: "123456" } });
  onSubmit.mockClear();
  expect(fireEvent.keyDown(input, { key: "Enter", ...init })).toBe(false);
  expect(onSubmit).not.toHaveBeenCalled();
  // a plain Enter is left alone
  expect(fireEvent.keyDown(input, { key: "Enter" })).toBe(true);
});
