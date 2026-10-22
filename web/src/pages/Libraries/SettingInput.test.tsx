import { fireEvent, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { SettingInput } from "./SettingInput";

test.each([
  ["isComposing", { isComposing: true }],
  ["keyCode 229", { keyCode: 229 }],
])("an Enter that confirms an IME composition (%s) does not commit the setting", (_n, init) => {
  const onCommit = vi.fn((v: string) => Promise.resolve(v));
  renderWithProviders(<SettingInput label="间隔" server="60" onCommit={onCommit} />);
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "30" } });
  fireEvent.keyDown(input, { key: "Enter", ...init });
  expect(onCommit).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onCommit).toHaveBeenCalledWith("30");
});
