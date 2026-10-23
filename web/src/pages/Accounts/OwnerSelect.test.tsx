import { screen, waitFor } from "@testing-library/react";
import { HttpResponse } from "msw";
import { useState } from "react";
import { describe, expect, test } from "vitest";
import { calls, db, given } from "../../test/msw";
import { renderWithProviders } from "../../test/render";
import { AUTO_OWNER, OwnerSelect, type OwnerChoice } from "./OwnerSelect";

let last: OwnerChoice = AUTO_OWNER;

function Harness({ initial }: { initial: string }) {
  const [folder, setFolder] = useState(initial);
  const [value, setValue] = useState<OwnerChoice>(AUTO_OWNER);
  last = value;
  return (
    <>
      <input aria-label="folder" value={folder} onChange={(e) => setFolder(e.target.value)} />
      <OwnerSelect
        folderRel={folder}
        value={value}
        onChange={(v) => {
          last = v;
          setValue(v);
        }}
      />
    </>
  );
}

const select = () => screen.findByRole("combobox", { name: "文件属主" });

describe("OwnerSelect", () => {
  test("follows the folder's suggested owner until the user picks one, then sticks", async () => {
    const { user } = renderWithProviders(<Harness initial="Alice/Photos/iCloud" />);
    expect(await select()).toHaveDisplayValue("Alice (1000:10 · admin)");
    expect(last).toEqual({ owner: { uid: 1000, gid: 10 }, picked: false, valid: true });

    const folder = screen.getByRole("textbox", { name: "folder" });
    await user.clear(folder);
    await user.type(folder, "Bob/Photos/iCloud");
    await waitFor(() => expect(screen.getByRole("combobox", { name: "文件属主" })).toHaveDisplayValue("Bob (1001:10 · admin)"));
    // debounced: one lookup for the finished folder, not one per keystroke
    const lookups = calls().filter((c) => c.path === "/api/host-users" && c.search.includes("Bob"));
    expect(lookups.length).toBeLessThanOrEqual(2);

    await user.selectOptions(screen.getByRole("combobox", { name: "文件属主" }), "1000:10");
    expect(last).toEqual({ owner: { uid: 1000, gid: 10 }, picked: true, valid: true });
    await user.clear(folder);
    await user.type(folder, "Bob/Other");
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/api/host-users" && decodeURIComponent(c.search) === "?path=Bob/Other")).toBe(true),
    );
    expect(screen.getByRole("combobox", { name: "文件属主" })).toHaveDisplayValue("Alice (1000:10 · admin)");
  });

  test("is hidden entirely in single-user mode", async () => {
    db.privileged = false;
    renderWithProviders(<Harness initial="Alice/Photos" />);
    await waitFor(() => expect(calls().some((c) => c.path === "/api/host-users")).toBe(true));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("combobox", { name: "文件属主" })).not.toBeInTheDocument();
    expect(screen.queryByText("文件属主")).not.toBeInTheDocument();
    expect(last.owner).toBeNull();
  });

  test("without host users it is a uid:gid input, validated", async () => {
    db.hostUsers = [];
    const { user } = renderWithProviders(<Harness initial="Alice/Photos" />);
    const input = await screen.findByRole("textbox", { name: "文件属主" });
    await user.type(input, "abc");
    expect(await screen.findByRole("alert")).toHaveTextContent("请填 uid:gid");
    expect(last.valid).toBe(false);
    await user.clear(input);
    await user.type(input, "1000:10");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(last).toEqual({ owner: { uid: 1000, gid: 10 }, picked: true, valid: true });
  });

  test("a root suggestion (folder not there yet under a root-owned /home) warns under the control", async () => {
    given("GET", "/api/host-users", {
      privileged: true,
      users: db.hostUsers,
      suggested: { uid: 0, gid: 0 },
    });
    renderWithProviders(<Harness initial="NewUser/Photos" />);
    expect(await screen.findByText(/文件会归 root 所有/)).toBeInTheDocument();
    expect(await select()).toHaveDisplayValue("0:0");
  });
  test("a pick made while the next folder's suggestion is in flight survives its late arrival", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    given("GET", "/api/host-users", async ({ request }: { request: Request }) => {
      const path = new URL(request.url).searchParams.get("path") ?? "";
      if (path.startsWith("Bob")) await gate;
      const user = db.hostUsers.find((u) => u.name === path.split("/")[0]);
      return HttpResponse.json({
        privileged: true,
        users: db.hostUsers,
        suggested: user ? { uid: user.uid, gid: user.gid } : null,
      });
    });
    const { user } = renderWithProviders(<Harness initial="Alice/Photos" />);
    expect(await select()).toHaveDisplayValue("Alice (1000:10 · admin)");
    const folder = screen.getByRole("textbox", { name: "folder" });
    await user.clear(folder);
    await user.type(folder, "Bob/Photos");
    await waitFor(() =>
      expect(calls().some((c) => c.path === "/api/host-users" && c.search.includes("Bob"))).toBe(true),
    );
    await user.selectOptions(screen.getByRole("combobox", { name: "文件属主" }), "1000:10");
    release();
    await waitFor(() => expect(last.picked).toBe(true));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByRole("combobox", { name: "文件属主" })).toHaveDisplayValue("Alice (1000:10 · admin)");
    expect(last).toEqual({ owner: { uid: 1000, gid: 10 }, picked: true, valid: true });
  });
});
