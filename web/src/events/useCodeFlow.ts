import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { useRuns, useStartAuth, useSubmitCode } from "../api/queries";
import { appEvents } from "./useEventStream";
import { useT } from "../i18n";

interface MfaEvent {
  run_id: string;
  event: { ev: string; ok?: boolean; error?: unknown };
}

/**
 * The 2FA code flow of one account (same rules as the overview card): submit a code, show a wrong
 * code in place and bump `attempt`, resend through `/auth`. Worker events carry only a run id, so
 * they are ours when the run is in the account's runs list or was started here (`runId`/`track`).
 * `authWaiting`: an auth run of this account is live, so `/auth` would only answer 400 — consumers
 * hide 重新发送 then.
 */
export function useCodeFlow(accountId: string, opts: { runId?: string; onNeedCode?: () => void } = {}) {
  const t = useT();
  const submit = useSubmitCode();
  const start = useStartAuth();
  const runs = useRuns({ account_id: accountId });
  const [wrong, setWrong] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const startedHere = useRef(new Set<string>(opts.runId ? [opts.runId] : []));
  const ownRuns = useRef(new Set<string>());
  ownRuns.current = new Set((runs.data ?? []).map((r) => r.id));
  const onNeedCode = useRef(opts.onNeedCode);
  onNeedCode.current = opts.onNeedCode;

  useEffect(() => {
    const onEvent = (e: Event) => {
      const { run_id, event } = (e as CustomEvent<MfaEvent>).detail;
      if (!ownRuns.current.has(run_id) && !startedHere.current.has(run_id)) return;
      if (event.ev === "need_mfa") onNeedCode.current?.();
      if (event.ev === "mfa_result" && event.ok === false) {
        const reason = typeof event.error === "string" ? event.error : "";
        setWrong(reason ? t("wrong-code", { reason }) : t("wrong-code-bare"));
        setAttempt((n) => n + 1);
      }
    };
    appEvents.addEventListener("run.event", onEvent);
    return () => appEvents.removeEventListener("run.event", onEvent);
  }, [t]);

  const authWaiting = (runs.data ?? []).some(
    (r) => r.mode === "auth" && (r.status === "running" || r.status === "needs_mfa"),
  );
  const detail = (e: unknown) => (e instanceof ApiError ? e.detail : null);
  const startMessage = (e: unknown) =>
    e instanceof ApiError && e.status === 400 && /already active/i.test(e.detail)
      ? t("auth-already-waiting")
      : message(e);
  const message = (e: unknown) => (e instanceof ApiError ? e.detail : e instanceof Error ? e.message : null);
  return {
    sendCode: (code: string) => {
      setWrong(null);
      submit.mutate({ id: accountId, code }, { onError: () => setAttempt((n) => n + 1) });
    },
    /** Start a fresh login (`/auth`); `onStarted` runs once the server accepted it. */
    resend: (onStarted?: () => void) => {
      setWrong(null);
      submit.reset();
      start.mutate(accountId, {
        onSuccess: (r) => {
          startedHere.current.add(r.run_id);
          onStarted?.();
        },
      });
    },
    track: (runId: string) => void startedHere.current.add(runId),
    error: wrong ?? detail(submit.error),
    /** Failures to show apart from the code field: a failed resend, or a non-API error on submit. */
    actionError: startMessage(start.error) ?? (submit.error instanceof ApiError ? null : message(submit.error)),
    attempt,
    pending: submit.isPending,
    resending: start.isPending,
    authWaiting,
  };
}
