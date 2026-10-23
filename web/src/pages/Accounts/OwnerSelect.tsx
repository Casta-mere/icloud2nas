import { useEffect, useState } from "react";
import { useHostUsers } from "../../api/queries";
import type { Owner } from "../../api/types";
import { useT } from "../../i18n";
import { FormRow } from "../../ui/FormRow";

/**
 * The file-owner choice of an account: `owner` is what to send (null = let the server default),
 * `picked` = the user chose it (from then on it no longer follows the folder), `valid` = the
 * typed `uid:gid` parses.
 */
export interface OwnerChoice {
  owner: Owner | null;
  picked: boolean;
  valid: boolean;
}

export const AUTO_OWNER: OwnerChoice = { owner: null, picked: false, valid: true };

const INPUT = "w-full min-w-0 rounded-[10px] border-2 border-ink bg-white px-2.5 py-1.5 text-[13px]";
const fmt = (o: Owner | null) => (o ? `${o.uid}:${o.gid}` : "");
const same = (a: Owner | null, b: Owner | null) => fmt(a) === fmt(b);

function parse(text: string): Owner | null {
  const m = /^(\d+):(\d+)$/.exec(text.trim());
  return m ? { uid: Number(m[1]), gid: Number(m[2]) } : null;
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return settled;
}

/**
 * 文件属主 (spec §16.3): a row in the card's label column. The default is the owner the server
 * suggests for `folderRel` (debounced while typing); it follows the folder until the user picks.
 * Hidden in single-user mode; a `uid:gid` input when the host has no users to list.
 */
export function OwnerSelect({
  folderRel,
  value,
  onChange,
}: {
  /** Photos-relative library folder to suggest an owner for; null = no suggestion. */
  folderRel: string | null;
  value: OwnerChoice;
  onChange: (next: OwnerChoice) => void;
}) {
  const t = useT();
  const path = useDebounced(folderRel, 300);
  const host = useHostUsers(path);
  const [text, setText] = useState(fmt(value.owner));
  const data = host.data;
  const suggested = data?.suggested ?? null;
  const settled = !host.isPlaceholderData;

  useEffect(() => {
    if (!settled || !data?.privileged || value.picked || same(value.owner, suggested)) return;
    onChange({ owner: suggested, picked: false, valid: true });
  }, [settled, data?.privileged, suggested, value.picked, value.owner, onChange]);

  if (!data?.privileged) return null;

  const warning = value.owner?.uid === 0 ? t("owner-root-warning") : undefined;

  if (data.users.length === 0) {
    const shown = value.picked ? text : fmt(value.owner);
    return (
      <FormRow
        label={t("file-owner")}
        hint={warning}
        error={value.valid ? null : t("owner-invalid")}
      >
        <input
          className={`${INPUT} font-mono`}
          inputMode="numeric"
          placeholder={"1000:10"}
          value={shown}
          onChange={(e) => {
            const next = e.target.value;
            setText(next);
            const owner = parse(next);
            onChange({ owner, picked: true, valid: owner !== null || next.trim() === "" });
          }}
        />
      </FormRow>
    );
  }

  const current = fmt(value.owner);
  const listed = data.users.some((u) => fmt(u) === current);
  return (
    <FormRow label={t("file-owner")} hint={warning}>
      <select
        className={INPUT}
        value={current}
        onChange={(e) => onChange({ owner: parse(e.target.value), picked: true, valid: true })}
      >
        {value.owner === null ? <option value="">{t("owner-default")}</option> : null}
        {value.owner !== null && !listed ? <option value={current}>{current}</option> : null}
        {data.users.map((u) => (
          <option key={`${u.uid}:${u.gid}`} value={`${u.uid}:${u.gid}`}>
            {`${u.name} (${u.uid}:${u.gid} · ${u.group})`}
          </option>
        ))}
      </select>
    </FormRow>
  );
}
