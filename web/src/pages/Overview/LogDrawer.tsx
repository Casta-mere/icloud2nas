import { ApiError } from "../../api/client";
import { useAccounts, useLibraries, useRunLog } from "../../api/queries";
import type { Run } from "../../api/types";
import { useT } from "../../i18n";
import { fmtDateTime, fmtDuration, fmtTime } from "../../lib/format";
import { libraryName } from "../../lib/names";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { Drawer } from "../../ui/Drawer";

const num = (v: unknown): number | null => (typeof v === "number" ? v : null);

export function LogDrawer({ run, onClose }: { run: Run; onClose: () => void }) {
  const t = useT();
  const libraries = useLibraries();
  const accounts = useAccounts();
  const log = useRunLog(run.id, { refetchInterval: run.status === "running" ? 3000 : false });

  const where = run.library_id
    ? libraryName(libraries.data, run.library_id)
    : (accounts.data?.find((a) => a.id === run.account_id)?.display_name ?? "—");

  const stats: string[] = [];
  const add = (key: "stat-downloaded" | "stat-trashed" | "stat-remote" | "stat-local", v: unknown) => {
    const n = num(v);
    if (n !== null) stats.push(t(key, { n }));
  };
  if (run.mode === "sync") {
    add("stat-downloaded", run.stats.downloaded);
    add("stat-trashed", run.stats.trashed);
    if (run.stats.full === true) stats.push(t("stat-full"));
  } else if (run.mode === "reconcile") {
    add("stat-remote", run.stats.remote_total);
    add("stat-local", run.stats.local_total);
    add("stat-trashed", run.stats.trashed);
  }

  const text = log.data ?? "";
  const status = run.status === "failed" && run.error_kind === "killed" ? "killed" : run.status;

  return (
    <Drawer
      open
      onClose={onClose}
      title={`${where} · ${t(`mode-${run.mode}`)} · ${t(`trigger-${run.trigger}`)}`}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Chip tone={run.status === "ok" ? "ok" : run.status === "running" ? "neutral" : "fuse"}>
          {t(`run-status-${status}`)}
          {run.exit_code !== null ? ` · ${t("exit-code", { n: run.exit_code })}` : ""}
        </Chip>
        <span className="min-w-0 text-[12px] text-[#6b7280] tabular-nums">
          {fmtDateTime(run.started_at)}
          {run.ended_at ? ` → ${fmtTime(run.ended_at)}` : ""} · {fmtDuration(run.started_at, run.ended_at)}
        </span>
      </div>
      {stats.length > 0 ? (
        <div className="mt-1.5 text-[12px] text-[#374151] tabular-nums">{stats.join(" · ")}</div>
      ) : null}
      {log.error ? (
        <div role="alert" className="mt-2 text-[12px] text-[#991b1b]">
          {t("error-prefix")}
          {log.error instanceof ApiError ? log.error.detail : log.error.message}
        </div>
      ) : null}
      <pre className="mt-2 max-h-[60vh] overflow-auto rounded-[10px] bg-[#111] p-3 font-mono text-[11.5px] leading-[1.5] break-words whitespace-pre-wrap text-[#e5e7eb]">
        {text || (log.isPending ? "" : t("log-empty"))}
      </pre>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button variant="ghost" onClick={() => void navigator.clipboard.writeText(text)}>
          {t("log-copy")}
        </Button>
        <a
          href={`/api/runs/${run.id}/log`}
          download
          className="inline-flex items-center rounded-[10px] border-2 border-[#cbd5e1] bg-white px-3 py-1.5 text-[13px] font-semibold whitespace-nowrap"
        >
          {t("log-download")}
        </a>
        <span className="ml-auto text-[12px] text-[#6b7280]">{t("log-tail-note")}</span>
      </div>
    </Drawer>
  );
}
