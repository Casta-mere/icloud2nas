import { useId } from "react";
import { useT } from "../../i18n";
import { Button } from "../../ui/Button";

/**
 * The unsaved-changes bar of the Settings page: absent while nothing is unsaved, otherwise pinned
 * to the bottom of the viewport until save or discard. On phones it floats just above BottomTabs
 * (its height: 2px border + 10px top padding + 16.5px line + max(12px, safe area) bottom padding).
 * A spacer at the end of the page lets the last card scroll clear of it.
 */
export function SaveBar({
  count,
  canSave,
  error,
  onDiscard,
  onSave,
}: {
  count: number;
  canSave: boolean;
  /** A server error that belongs to no field; shown inside the bar. */
  error: string | null;
  onDiscard: () => void;
  onSave: () => void;
}) {
  const t = useT();
  const label = useId();
  if (count === 0) return null;
  return (
    <>
      <div aria-hidden="true" className="h-20" />
      <div
        role="region"
        aria-labelledby={label}
        className="fixed inset-x-0 bottom-[calc(37px_+_max(12px,env(safe-area-inset-bottom)))] z-30 sm:bottom-4"
      >
        <div className="mx-auto max-w-[1180px] px-3.5 sm:px-[26px]">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-card border-2 border-ink bg-[rgba(255,255,255,.92)] px-3.5 py-2.5 shadow-card backdrop-blur-[10px]">
            <span
              id={label}
              className="mr-auto inline-flex min-w-0 items-center gap-2 text-[13px] font-bold tabular-nums"
            >
              <span aria-hidden="true" className="inline-block h-2 w-2 shrink-0 rounded-full bg-[#f59e0b]" />
              <span className="truncate">{t(count === 1 ? "unsaved-one" : "unsaved-other", { n: count })}</span>
            </span>
            <div className="ml-auto flex gap-2">
              <Button variant="ghost" onClick={onDiscard}>
                {t("discard")}
              </Button>
              <Button variant="primary" disabled={!canSave} onClick={onSave}>
                {t("save")}
              </Button>
            </div>
            {error && (
              <span role="alert" className="order-last basis-full text-xs text-[#9a3412]">
                {t("err-form")}
                {error}
              </span>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
