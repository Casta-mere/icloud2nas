import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { FormRow } from "./FormRow";

test("the label names a bare child control", () => {
  render(
    <FormRow label="间隔" hint="分钟">
      <input />
    </FormRow>,
  );
  const input = screen.getByLabelText("间隔");
  expect(input.tagName).toBe("INPUT");
  expect(input.id).not.toBe("");
});

test("a child with its own id keeps it, and htmlFor overrides", () => {
  render(
    <>
      <FormRow label="名称">
        <input id="name-input" />
      </FormRow>
      <FormRow label="目录" htmlFor="dir-input">
        <div>
          <input id="dir-input" />
        </div>
      </FormRow>
    </>,
  );
  expect(screen.getByLabelText("名称")).toHaveAttribute("id", "name-input");
  expect(screen.getByLabelText("目录")).toHaveAttribute("id", "dir-input");
});
