import { useState } from "react";
import { useSettings, useTrash } from "../../api/queries";
import type { TrashEntry } from "../../api/types";
import { useT } from "../../i18n";
import { fmtBytes } from "../../lib/format";
import { Empty } from "../../ui/Empty";
import { DayTable } from "./DayTable";
import { TrashTree } from "./TrashTree";

export interface TrashSelection {
  rel: string;
  day: string;
}

const byText = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

/** Libraries in the API's order, then orphans by rel; days newest first. */
function ordered(entries: TrashEntry[]): TrashEntry[] {
  return entries
    .filter((e) => e.days.length > 0)
    .map((e) => ({ ...e, days: [...e.days].sort((a, b) => byText(b.day, a.day)) }))
    .sort((a, b) => {
      if (a.library_id === null && b.library_id === null) return byText(a.rel, b.rel);
      if (a.library_id === null || b.library_id === null) return a.library_id === null ? 1 : -1;
      return 0; // libraries keep the API's order
    });
}

export function Trash() {
  const t = useT();
  const trash = useTrash();
  const settings = useSettings();
  const [picked, setPicked] = useState<TrashSelection | null>(null);

  if (!trash.data) return null;
  const entries = ordered(trash.data);
  const total = trash.data.reduce((sum, e) => sum + e.days.reduce((s, d) => s + d.bytes, 0), 0);
  const first = entries[0];
  const valid = picked && entries.some((e) => e.rel === picked.rel && e.days.some((d) => d.day === picked.day));
  const selected: TrashSelection | null = valid
    ? picked
    : first && first.days[0]
      ? { rel: first.rel, day: first.days[0].day }
      : null;
  const entry = entries.find((e) => e.rel === selected?.rel);

  return (
    <div className="flex flex-col gap-3">
      <div className="truncate font-mono text-[13px] text-[#374151]">
        {t("trash-prompt", { bytes: fmtBytes(total), days: settings.data?.values.trash_retention_days ?? "…" })}
      </div>
      {selected && entry ? (
        <div className="grid items-start gap-4 min-[900px]:grid-cols-[300px_minmax(0,1fr)]">
          <TrashTree entries={entries} selected={selected} onSelect={setPicked} />
          <DayTable key={`${selected.rel}|${selected.day}`} entry={entry} day={selected.day} />
        </div>
      ) : (
        <Empty text={t("trash-empty")} />
      )}
    </div>
  );
}
