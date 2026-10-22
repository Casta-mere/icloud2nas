import { ESLint } from "eslint";
import { expect, test } from "vitest";

// A real source path so the type-aware project service accepts the virtual text.
const FILE = `${process.cwd()}/src/pages/Overview/Overview.tsx`;

async function restricted(code: string) {
  const [result] = await new ESLint().lintText(code, { filePath: FILE });
  return (result?.messages ?? []).filter((m) => m.ruleId === "no-restricted-syntax");
}

test("no JSXText with letters outside t()", async () => {
  const bad = await restricted(`export const A = () => <p>Hello</p>;\nexport const B = () => <p>你好</p>;\n`);
  expect(bad).toHaveLength(2);
  const good = await restricted(
    `export const A = () => <p>{"Hello"} · {1} /{" "}</p>;\n`,
  );
  expect(good).toEqual([]);
}, 60_000);
