import type { TrashEntry } from "../../api/types";
import { useT } from "../../i18n";
import { fmtBytes } from "../../lib/format";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import type { TrashSelection } from "./Trash";

export function TrashTree({
  entries,
  selected,
  onSelect,
}: {
  entries: TrashEntry[];
  selected: TrashSelection | null;
  onSelect: (s: TrashSelection) => void;
}) {
  const t = useT();
  return (
    <Card>
      <nav aria-label={t("trash-tree")} className="flex flex-col gap-1">
        <div className="mb-1 text-xs tracking-[.06em] text-[#374151] uppercase">{t("trash-tree")}</div>
        {entries.map((e) => (
          <div key={e.library_id} className="mt-1.5 flex min-w-0 flex-col gap-1 first:mt-0">
            <h3 className="flex min-w-0 items-center gap-2 text-[13px] font-bold">
              <span className="truncate">{e.display_name}</span>
            </h3>
            {e.days.map((d) => {
              const on = selected?.library === e.library_id && selected.day === d.day;
              return (
                <Button
                  key={d.day}
                  variant="ghost"
                  className={`w-full justify-between! tabular-nums ${on ? "border-ink!" : ""}`}
                  onClick={() => onSelect({ library: e.library_id, day: d.day })}
                  selected={on}
                >
                  <span>{d.day}</span>
                  <small className="font-normal text-[#6b7280]">
                    {t("trash-day-meta", { files: d.files, bytes: fmtBytes(d.bytes) })}
                  </small>
                </Button>
              );
            })}
          </div>
        ))}
      </nav>
    </Card>
  );
}
