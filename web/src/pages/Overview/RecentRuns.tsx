import type { Account, Library, Run } from "../../api/types";
import { useT } from "../../i18n";
import { fmtTime } from "../../lib/format";
import { accountName, libraryName } from "../../lib/names";
import { Card } from "../../ui/Card";
import { Chip, type ChipTone } from "../../ui/Chip";
import { Empty } from "../../ui/Empty";

const TONE: Record<Run["status"], ChipTone> = {
  queued: "neutral",
  running: "ok",
  ok: "ok",
  failed: "fuse",
  needs_mfa: "warn",
  fused: "fuse",
};

export function RecentRuns({
  runs,
  libraries,
  accounts,
  onOpen,
}: {
  runs: Run[];
  libraries: Library[];
  accounts: Account[];
  onOpen: (run: Run) => void;
}) {
  const t = useT();
  const rows = [...runs].sort((a, b) => b.started_at.localeCompare(a.started_at)).slice(0, 20);
  return (
    <Card>
      <h2 className="m-0 mb-2 text-[15px] font-extrabold">{t("recent-runs")}</h2>
      {rows.length === 0 ? (
        <Empty text={t("runs-empty")} />
      ) : (
        <ul aria-label={t("recent-runs")} className="m-0 list-none p-0">
          {rows.map((run) => {
            const killed = run.status === "failed" && run.error_kind === "killed";
            const who = run.library_id
              ? libraryName(libraries, run.library_id)
              : accountName(accounts, run.account_id);
            return (
              <li key={run.id}>
                <button
                  type="button"
                  onClick={() => onOpen(run)}
                  className="grid w-full cursor-pointer grid-cols-[52px_minmax(0,1fr)_auto] items-center gap-x-2 border-0 border-b border-[rgba(17,17,17,.1)] bg-transparent px-0 py-1.5 text-left text-[13px] whitespace-nowrap"
                >
                  <span data-testid="run-time" className="truncate text-[#6b7280] tabular-nums">
                    {fmtTime(run.started_at)}
                  </span>
                  <span className="truncate">
                    {who} · {t(`mode-${run.mode}`)}
                  </span>
                  <Chip tone={TONE[run.status]}>
                    {t(killed ? "run-status-killed" : `run-status-${run.status}`)}
                  </Chip>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
