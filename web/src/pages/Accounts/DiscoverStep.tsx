import { useEffect, useState } from "react";
import { useLibraries, useRefreshAccount, useRuns } from "../../api/queries";
import { ApiError } from "../../api/client";
import type { EventPayloads } from "../../api/types";
import { appEvents } from "../../events/useEventStream";
import { useT } from "../../i18n";
import { Button } from "../../ui/Button";

/** Wizard step 4: waits for the list run, shows the discovered libraries, `not_activated` → retry. */
export function DiscoverStep({ accountId, onDone }: { accountId: string; onDone: () => void }) {
  const t = useT();
  const libraries = useLibraries();
  const runs = useRuns({ account_id: accountId });
  const refresh = useRefreshAccount();
  const { refetch: refetchRuns } = runs;
  const { refetch: refetchLibraries } = libraries;
  const [listFinished, setListFinished] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(null);

  useEffect(() => {
    const onFinished = (e: Event) => {
      const p = (e as CustomEvent<EventPayloads["run.finished"]>).detail;
      if (p.mode !== "list" || p.account_id !== accountId) return;
      setListFinished(true);
      // the stream invalidates these too; refetching here keeps the step correct on its own
      void refetchRuns();
      void refetchLibraries();
    };
    appEvents.addEventListener("run.finished", onFinished);
    return () => appEvents.removeEventListener("run.finished", onFinished);
  }, [accountId, refetchRuns, refetchLibraries]);

  const mine = (libraries.data ?? []).filter((l) => l.account_id === accountId);
  const listRun = (runs.data ?? []).find((r) => r.mode === "list");
  const failed =
    mine.length === 0 && listRun?.status === "failed" && listRun.id !== dismissed ? listRun : null;
  const notActivated = failed?.error_kind === "not_activated";
  const discovering = mine.length === 0 && !failed && !listFinished;
  const retry = () =>
    refresh.mutate(accountId, {
      onSuccess: () => {
        setDismissed(listRun?.id ?? null);
        setListFinished(false);
      },
    });

  return (
    <div>
      {discovering ? <p className="m-0 text-[13px] text-[#374151]">{t("discovering")}</p> : null}
      {failed ? (
        <div className="grid gap-2">
          <p className="m-0 text-[13px] text-[#9a3412]">
            {notActivated
              ? t("indexing-wait")
              : failed.error_message
                ? t("discover-failed-detail", { detail: failed.error_message })
                : t("discover-failed")}
          </p>
          <div>
            <Button disabled={refresh.isPending} onClick={retry}>
              {t("retry")}
            </Button>
          </div>
        </div>
      ) : null}
      {refresh.error instanceof ApiError ? (
        <div role="alert" className="mt-2 text-[12px] text-[#991b1b]">
          {refresh.error.detail}
        </div>
      ) : null}
      <div className="mt-2 grid gap-2">
        {mine.map((lib) => (
          <div key={lib.id} className="grid grid-cols-[96px_minmax(0,1fr)] items-baseline gap-x-2">
            <b className="truncate text-[13px]">{lib.display_name}</b>
            <span className="truncate font-mono text-[12px] text-[#4b5563]">{`/photos/${lib.target_dir}`}</span>
          </div>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button variant="primary" onClick={onDone}>
          {t("done")}
        </Button>
      </div>
    </div>
  );
}
