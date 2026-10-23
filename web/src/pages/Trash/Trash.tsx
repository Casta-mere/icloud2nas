import { useState } from "react";
import { useSettings, useTrash } from "../../api/queries";
import type { TrashEntry } from "../../api/types";
import { useT } from "../../i18n";
import { fmtBytes } from "../../lib/format";
import { Empty } from "../../ui/Empty";
import { QueryState } from "../../ui/QueryState";
import { DayTable } from "./DayTable";
import { TrashTree } from "./TrashTree";

export interface TrashSelection {
  library: string;
  day: string;
}

const byText = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

/** Libraries with trash, in the API's order; days newest first. */
function ordered(entries: TrashEntry[]): TrashEntry[] {
  return entries
    .filter((e) => e.days.length > 0)
    .map((e) => ({ ...e, days: [...e.days].sort((a, b) => byText(b.day, a.day)) }));
}

export function Trash() {
  const t = useT();
  const trash = useTrash();
  const settings = useSettings();
  const [picked, setPicked] = useState<TrashSelection | null>(null);

  if (!trash.data) return <QueryState queries={[trash]} />;
  const days = settings.data?.values.trash_retention_days;
  const retention = days === 0 ? t("retention-forever") : t("trash-keep-days", { days: days ?? "…" });
  const entries = ordered(trash.data);
  const total = trash.data.reduce((sum, e) => sum + e.days.reduce((s, d) => s + d.bytes, 0), 0);
  const first = entries[0];
  const valid = picked && entries.some((e) => e.library_id === picked.library && e.days.some((d) => d.day === picked.day));
  const selected: TrashSelection | null = valid
    ? picked
    : first && first.days[0]
      ? { library: first.library_id, day: first.days[0].day }
      : null;
  const entry = entries.find((e) => e.library_id === selected?.library);

  return (
    <div className="flex flex-col gap-3">
      <div className="truncate font-mono text-[13px] text-[#374151]">
        {t("trash-prompt", { bytes: fmtBytes(total), retention })}
      </div>
      {selected && entry ? (
        <div className="grid items-start gap-4 min-[900px]:grid-cols-[300px_minmax(0,1fr)]">
          <TrashTree entries={entries} selected={selected} onSelect={setPicked} />
          <DayTable key={`${selected.library}|${selected.day}`} entry={entry} day={selected.day} />
        </div>
      ) : (
        <Empty text={t("trash-empty")} />
      )}
    </div>
  );
}
