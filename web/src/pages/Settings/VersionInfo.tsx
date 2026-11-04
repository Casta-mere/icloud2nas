import { useRef, useState, type ReactNode } from "react";
import { ApiError } from "../../api/client";
import { useCheckForUpdates, useStatus } from "../../api/queries";
import { useT } from "../../i18n";
import { copyText } from "../../lib/clipboard";
import { fmtDuration } from "../../lib/format";
import { Button } from "../../ui/Button";
import { FormRow } from "../../ui/FormRow";

/** What the owner runs on the NAS; the app never updates itself. */
export const UPDATE_COMMAND = "docker compose pull && docker compose up -d";

/**
 * The About card's version row: the running version and 检查更新, then one status line (checking,
 * up to date, or why the last check failed) and, when a newer release is out, how to update.
 * A failed check after a successful one shows both: the error and the release still known.
 */
export function VersionInfo() {
  const t = useT();
  const status = useStatus();
  const check = useCheckForUpdates();
  const codeRef = useRef<HTMLElement>(null);
  const [copied, setCopied] = useState<boolean | null>(null);

  const update = status.data?.update;
  const requestError = check.error
    ? check.error instanceof ApiError
      ? check.error.detail
      : check.error.message
    : null;
  const error = requestError ?? update?.error ?? null;

  let line: ReactNode = null;
  if (check.isPending) {
    line = t("update-checking");
  } else if (error) {
    line = <span className="text-[#9a3412]">{t("update-failed", { error })}</span>;
  } else if (update?.checked_at && !update.update_available) {
    line = (
      <span className="text-[#166534]">
        <span aria-hidden="true">{"✓ "}</span>
        {t("update-latest", { ago: fmtDuration(update.checked_at, null) })}
      </span>
    );
  }

  const copy = () => {
    void copyText(UPDATE_COMMAND).then((ok) => {
      setCopied(ok);
      // at worst the command is selected, ready for the owner's own copy
      if (!ok && codeRef.current) window.getSelection()?.selectAllChildren(codeRef.current);
    });
  };

  const latest = update?.update_available ? update.latest : null;
  const howTo = latest && (
    <div className="grid gap-1.5">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <strong className="text-[13px] text-[#166534]">{t("update-available", { latest })}</strong>
        {update?.compare_url && (
          <a
            href={update.compare_url}
            target="_blank"
            rel="noreferrer"
            className="font-bold whitespace-nowrap text-ink underline"
          >
            {t("update-whats-new")}
            <span aria-hidden="true">{" ↗"}</span>
          </a>
        )}
      </div>
      <span>{t("update-command")}</span>
      <div className="flex flex-wrap items-center gap-2">
        <code
          ref={codeRef}
          className="min-w-0 rounded-lg border-[1.5px] border-ink bg-white px-2 py-1 font-mono text-xs text-ink select-all"
        >
          {UPDATE_COMMAND}
        </code>
        <Button variant="ghost" onClick={copy} className="px-2.5 py-1 text-xs">
          {t("update-copy")}
        </Button>
        {copied !== null && (
          <span role="status" className={copied ? "text-[#166534]" : "text-[#9a3412]"}>
            {copied ? t("update-copied") : t("update-copy-failed")}
          </span>
        )}
      </div>
    </div>
  );

  return (
    <FormRow
      label={t("version-label")}
      htmlFor=""
      hint={
        <div className="grid gap-1.5">
          <span role="status">{line}</span>
          {howTo}
        </div>
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[13px] tabular-nums">{status.data?.version ?? "—"}</span>
        <Button variant="ghost" disabled={check.isPending} onClick={() => check.mutate()}>
          {t("update-check")}
        </Button>
      </div>
    </FormRow>
  );
}
