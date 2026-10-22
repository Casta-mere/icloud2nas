import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import {
  usePatchLibrary,
  usePauseLibrary,
  useRequestRun,
  useResumeLibrary,
  useRuns,
} from "../../api/queries";
import type { Account, Library, LibraryPatch, Plan } from "../../api/types";
import { useT } from "../../i18n";
import { fmtTime } from "../../lib/format";
import { libraryChip } from "../../lib/status";
import { Button } from "../../ui/Button";
import { Card, type CardTone } from "../../ui/Card";
import { Chip } from "../../ui/Chip";
import { Confirm } from "../../ui/Confirm";
import { FormRow } from "../../ui/FormRow";
import { Toggle } from "../../ui/Toggle";
import { PlanCard } from "./PlanCard";
import { SettingInput } from "./SettingInput";

function cardTone(library: Library, hasPlan: boolean): CardTone {
  if (hasPlan) return "fuse";
  if (library.status === "space_paused") return "warn";
  return library.status === "paused" ? "soft" : "ok";
}

const detailOf = (e: unknown) => (e instanceof ApiError ? e.detail : null);
const CANCELLED = new Error("cancelled");

export function LibraryCard({ library, account, plan }: { library: Library; account: Account; plan?: Plan }) {
  const t = useT();
  const patch = usePatchLibrary();
  const run = useRequestRun();
  const pause = usePauseLibrary();
  const resume = useResumeLibrary();
  const runs = useRuns({ limit: 50 });
  const [menu, setMenu] = useState(false);
  const [enabledError, setEnabledError] = useState<string | null>(null);
  const [ask, setAsk] = useState<{ to: string; ok: (v: string) => void; fail: (e: unknown) => void } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    const onDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const running = (runs.data ?? []).some((r) => r.status === "running" && r.library_id === library.id);
  const chip = libraryChip(library, running, plan !== undefined);
  const paused = library.status !== "ok";
  const files = library.stats.files;

  const save = async (body: LibraryPatch): Promise<Library> => patch.mutateAsync({ id: library.id, ...body });
  const askDir = (to: string) =>
    new Promise<string>((ok, fail) => setAsk({ to, ok, fail }));
  const confirmDir = () => {
    if (!ask) return;
    const { to, ok, fail } = ask;
    setAsk(null);
    save({ target_dir: to }).then((l) => ok(l.target_dir), fail);
  };
  const cancelDir = () => {
    ask?.fail(CANCELLED);
    setAsk(null);
  };

  const actionError = detailOf(run.error) ?? detailOf(pause.error) ?? detailOf(resume.error);

  return (
    <Card tone={cardTone(library, plan !== undefined)}>
      <article aria-label={library.display_name}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="m-0 flex min-w-0 flex-1 basis-[160px] items-center gap-2 text-[22px] leading-tight font-extrabold">
            <span className="truncate">{library.display_name}</span>
            <Chip tone={chip.tone}>{t(chip.key)}</Chip>
          </h2>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" disabled={run.isPending} onClick={() => run.mutate({ id: library.id, mode: "sync" })}>
              {t("sync-now")}
            </Button>
            <Button
              variant="ghost"
              disabled={run.isPending}
              onClick={() => run.mutate({ id: library.id, mode: "reconcile" })}
            >
              {t("reconcile-now")}
            </Button>
            <div ref={menuRef} className="relative">
              <Button variant="ghost" onClick={() => setMenu((m) => !m)}>
                <span aria-label={t("more")}>⋯</span>
              </Button>
              {menu ? (
                <div className="absolute top-full right-0 z-20 mt-1 grid min-w-[160px] gap-1 rounded-[10px] border-2 border-ink bg-white p-1">
                  {paused ? (
                    <>
                      <Button
                        variant="ghost"
                        className="w-full justify-start"
                        disabled={library.status === "space_paused"}
                        onClick={() => {
                          setMenu(false);
                          resume.mutate(library.id);
                        }}
                      >
                        {t("resume")}
                      </Button>
                      {library.status === "space_paused" ? (
                        <div className="px-2 pb-1 text-xs text-[#6b7280]">{t("space-paused-hint")}</div>
                      ) : null}
                    </>
                  ) : (
                    <Button
                      variant="ghost"
                      className="w-full justify-start"
                      onClick={() => {
                        setMenu(false);
                        pause.mutate(library.id);
                      }}
                    >
                      {t("pause")}
                    </Button>
                  )}
                </div>
              ) : null}
            </div>
          </div>
        </div>
        {actionError ? (
          <div role="alert" className="mt-2 text-[12px] text-[#9a3412]">
            {actionError}
          </div>
        ) : null}

        <div className="mt-3 grid gap-2">
          <FormRow label={t("lib-account")}>
            <span className="block truncate text-[13px]">
              {account.display_name} · <span className="font-mono">{account.apple_id}</span>
            </span>
          </FormRow>
          <FormRow label={t("lib-path")}>
            <span className="block truncate font-mono text-[13px]">{`/photos/${library.target_dir}`}</span>
          </FormRow>
          <FormRow label={t("lib-files")}>
            <span className="block truncate text-[13px] tabular-nums">
              {typeof files === "number" ? files : "—"} · {t("lib-last-success", { time: fmtTime(library.last_success_at) })} ·{" "}
              {t(library.first_full_at ? "first-full-done" : "first-full-none")}
            </span>
          </FormRow>
        </div>

        <div className="mt-3 grid gap-2.5">
          <FormRow label={t("lib-enabled")} error={enabledError}>
            <Toggle
              checked={library.enabled}
              onChange={(enabled) => {
                setEnabledError(null);
                save({ enabled }).catch((e: unknown) => setEnabledError(detailOf(e)));
              }}
            />
          </FormRow>
          <SettingInput
            label={t("lib-interval")}
            server={String(library.interval_min)}
            type="number"
            inputMode="numeric"
            min={5}
            max={1440}
            width="w-[90px]"
            suffix={t("lib-interval-unit")}
            onCommit={async (v) => String((await save({ interval_min: Number(v) })).interval_min)}
          />
          <SettingInput
            label={t("lib-nightly")}
            server={library.nightly_at}
            width="w-[90px]"
            onCommit={async (v) => (await save({ nightly_at: v })).nightly_at}
          />
          <SettingInput
            label={t("lib-dir")}
            server={library.target_dir}
            disabled={running}
            hint={running ? t("dir-locked-while-running") : t("dir-move-hint")}
            onCommit={askDir}
          />
          <SettingInput
            label={t("display-name")}
            server={library.display_name}
            onCommit={async (v) => (await save({ display_name: v })).display_name}
          />
        </div>

        {plan ? <PlanCard plan={plan} library={library} /> : null}
      </article>
      <Confirm
        open={ask !== null}
        title={t("dir-change-title")}
        body={t("rename-moves-folder", { from: `/photos/${library.target_dir}`, to: `/photos/${ask?.to ?? ""}` })}
        confirmLabel={t("rename-confirm")}
        onConfirm={confirmDir}
        onCancel={cancelDir}
      />
    </Card>
  );
}
