import { useEffect, useState } from "react";
import { useAccounts, useRuns } from "../../api/queries";
import type { EventPayloads } from "../../api/types";
import { appEvents } from "../../events/useEventStream";
import { useT } from "../../i18n";
import { Button } from "../../ui/Button";
import { CodeInput } from "../../ui/CodeInput";
import { useCodeFlow } from "../../events/useCodeFlow";

/** Wizard step 3: the code input bound to the new account; `onVerified` once the login succeeded. */
export function CodeStep({
  accountId,
  runId,
  onVerified,
  onCancel,
}: {
  accountId: string;
  runId: string;
  onVerified: () => void;
  onCancel: () => void;
}) {
  const t = useT();
  const accounts = useAccounts();
  const status = accounts.data?.find((a) => a.id === accountId)?.status;
  const flow = useCodeFlow(accountId, { runId, status });
  const runs = useRuns({ account_id: accountId });
  const { refetch: refetchRuns } = runs;
  const [failedRun, setFailedRun] = useState<string | null>(null);

  useEffect(() => {
    if (status === "ok") onVerified();
  }, [status, onVerified]);

  useEffect(() => {
    const onFinished = (e: Event) => {
      const p = (e as CustomEvent<EventPayloads["run.finished"]>).detail;
      if (p.mode !== "auth" || p.account_id !== accountId) return;
      if (p.exit_code === 0) {
        onVerified();
        return;
      }
      setFailedRun(p.run_id);
      void refetchRuns();
    };
    appEvents.addEventListener("run.finished", onFinished);
    return () => appEvents.removeEventListener("run.finished", onFinished);
  }, [accountId, onVerified, refetchRuns]);

  const failure = status === "password_rejected" ? null : failedRun;
  const failedMessage = runs.data?.find((r) => r.id === failure)?.error_message;

  return (
    <div>
      <div className="text-[13px] text-[#374151]">{t("mfa-sent")}</div>
      {status === "password_rejected" ? (
        <div role="alert" className="mt-2 text-[12px] text-[#991b1b]">
          {t("password-rejected-wizard")}
        </div>
      ) : null}
      {failure ? (
        <div role="alert" className="mt-2 text-[12px] text-[#991b1b]">
          {failedMessage ? t("auth-failed-detail", { detail: failedMessage }) : t("auth-failed")}
        </div>
      ) : null}
      <CodeInput
        onSubmit={flow.sendCode}
        error={flow.error ?? flow.actionError}
        attempt={flow.attempt}
        pending={flow.pending}
      />
      {flow.authWaiting ? (
        <div className="mt-2 text-[12px] text-[#6b7280]">{t("resend-after-expiry")}</div>
      ) : (
        <div className="mt-2 text-[12px] text-[#6b7280]">
          {t("code-no-receive")}
          <button
            type="button"
            disabled={flow.resending}
            onClick={() => {
              setFailedRun(null);
              flow.resend();
            }}
            className="cursor-pointer border-0 bg-transparent p-0 text-[12px] whitespace-nowrap text-[#6b7280] underline"
          >
            {t("code-resend")}
          </button>
        </div>
      )}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          {t("cancel")}
        </Button>
      </div>
    </div>
  );
}
