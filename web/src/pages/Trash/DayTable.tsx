import { useState } from "react";
import { ApiError } from "../../api/client";
import { useDeleteDay, useKeepFiles, useTrashDay } from "../../api/queries";
import type { TrashEntry } from "../../api/types";
import { useT } from "../../i18n";
import { fmtBytes } from "../../lib/format";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Chip } from "../../ui/Chip";
import { Confirm } from "../../ui/Confirm";

const ROW_CAP = 500;
const GRID = "grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-x-2";

export function DayTable({ entry, day }: { entry: TrashEntry; day: string }) {
  const t = useT();
  const files = useTrashDay(day, entry.rel);
  const keep = useKeepFiles();
  const del = useDeleteDay();
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const [confirming, setConfirming] = useState(false);

  const name = entry.display_name ?? entry.rel;
  const meta = entry.days.find((d) => d.day === day);
  const rows = files.data ?? [];
  const shown = rows.slice(0, ROW_CAP);
  const allChecked = rows.length > 0 && checked.size === rows.length;
  const error = [keep.error, del.error].map((e) => (e instanceof ApiError ? e.detail : null)).find(Boolean);

  const toggle = (rel: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (!next.delete(rel)) next.add(rel);
      return next;
    });

  const onKeep = () =>
    keep.mutate({ day, rel: entry.rel, paths: [...checked] }, { onSuccess: () => setChecked(new Set()) });
  const onDelete = () => {
    setConfirming(false);
    del.mutate({ day, rel: entry.rel });
  };

  return (
    <Card>
      <div className="mb-2 flex flex-wrap items-center gap-x-2.5 gap-y-2">
        <b className="min-w-0 flex-1 basis-[200px] truncate">{`${name} · ${day}`}</b>
        {meta ? <Chip>{t("files-and-bytes", { n: meta.files, bytes: fmtBytes(meta.bytes) })}</Chip> : null}
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" disabled={checked.size === 0 || keep.isPending} onClick={onKeep}>
            {t("keep-selected", { n: checked.size })}
          </Button>
          <Button variant="danger" disabled={del.isPending} onClick={() => setConfirming(true)}>
            {t("delete-day")}
          </Button>
        </div>
      </div>
      {error ? (
        <p role="alert" className="mb-2 text-xs text-[#991b1b]">
          {error}
        </p>
      ) : null}
      <div className="text-[13px] tabular-nums">
        <label className={`${GRID} border-b border-[#e5e7eb] py-1.5 text-xs text-[#6b7280]`}>
          <input
            type="checkbox"
            aria-label={t("select-all")}
            checked={allChecked}
            onChange={() => setChecked(allChecked ? new Set() : new Set(rows.map((r) => r.rel)))}
          />
          <span>{t("select-all")}</span>
          <span />
        </label>
        {shown.map((f) => (
          <label key={f.rel} className={`${GRID} cursor-pointer border-b border-[#f1f5f9] py-1.5`}>
            <input type="checkbox" aria-label={f.rel} checked={checked.has(f.rel)} onChange={() => toggle(f.rel)} />
            <span className="truncate font-mono text-xs" title={f.rel}>
              {f.rel}
            </span>
            <span className="text-right tabular-nums">{fmtBytes(f.size)}</span>
          </label>
        ))}
        {rows.length > shown.length ? (
          <div className="py-1.5 text-xs text-[#6b7280]">{t("plan-more", { n: rows.length - shown.length })}</div>
        ) : null}
      </div>
      <p className="mt-2.5 text-xs text-[#6b7280]">{t("kept-explainer", { rel: entry.rel })}</p>
      <Confirm
        open={confirming}
        danger
        title={t("delete-day-title", { name, day })}
        body={t("delete-day-body", { n: meta?.files ?? rows.length })}
        confirmLabel={t("delete-day")}
        onConfirm={onDelete}
        onCancel={() => setConfirming(false)}
      />
    </Card>
  );
}
