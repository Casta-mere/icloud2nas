import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { useRuns, useStartAuth, useSubmitCode } from "../api/queries";
import type { AccountStatus, EventPayloads } from "../api/types";
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
 * `authWaiting`: an auth run of this account is running, so `/auth` would only answer 400 —
 * consumers show 验证码过期后可重新发送 in place of 重新发送 then. `sessionStillValid`: a login
 * started here finished ok without ever asking for a code (true for 5 s); `status` (the
 * account's) lets an `awaiting_code` stand in for a missed `need_mfa`.
 */
export function useCodeFlow(
  accountId: string,
  opts: { runId?: string; onNeedCode?: () => void; status?: AccountStatus } = {},
) {
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
  const neededCode = useRef(new Set<string>());
  const settled = useRef(new Set<string>());
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [sessionStillValid, setSessionStillValid] = useState(false);
  const [tracked, setTracked] = useState(0);
  const trackRun = (runId: string) => {
    startedHere.current.add(runId);
    setTracked((n) => n + 1);
  };

  // a login that waited for a code is never "still valid", even if its need_mfa event was missed
  useEffect(() => {
    if (opts.status === "awaiting_code") {
      for (const id of startedHere.current) if (!settled.current.has(id)) neededCode.current.add(id);
    }
  }, [opts.status]);

  /** One login started here ended (from its run.finished, or found finished in the runs list). */
  const settle = useRef((runId: string, ok: boolean) => {
    if (!startedHere.current.has(runId) || settled.current.has(runId)) return;
    settled.current.add(runId);
    if (!ok || neededCode.current.has(runId)) return;
    setSessionStillValid(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setSessionStillValid(false), 5_000);
  });

  useEffect(() => {
    const onFinished = (e: Event) => {
      const p = (e as CustomEvent<EventPayloads["run.finished"]>).detail;
      if (p.mode === "auth") settle.current(p.run_id, p.status === "ok");
    };
    appEvents.addEventListener("run.finished", onFinished);
    return () => {
      appEvents.removeEventListener("run.finished", onFinished);
      clearTimeout(timer.current);
    };
  }, []);

  // fallback for a missed run.finished: the runs list already shows the login ended
  useEffect(() => {
    for (const r of runs.data ?? []) {
      if (r.mode === "auth" && r.status !== "running" && r.status !== "queued") {
        settle.current(r.id, r.status === "ok");
      }
    }
  }, [runs.data, tracked]);

  useEffect(() => {
    const onEvent = (e: Event) => {
      const { run_id, event } = (e as CustomEvent<MfaEvent>).detail;
      if (!ownRuns.current.has(run_id) && !startedHere.current.has(run_id)) return;
      if (event.ev === "need_mfa") {
        neededCode.current.add(run_id);
        onNeedCode.current?.();
      }
      if (event.ev === "mfa_result" && event.ok === false) {
        const reason = typeof event.error === "string" ? event.error : "";
        setWrong(reason ? t("wrong-code", { reason }) : t("wrong-code-bare"));
        setAttempt((n) => n + 1);
      }
    };
    appEvents.addEventListener("run.event", onEvent);
    return () => appEvents.removeEventListener("run.event", onEvent);
  }, [t]);

  const authWaiting = (runs.data ?? []).some((r) => r.mode === "auth" && r.status === "running");
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
      setSessionStillValid(false);
      submit.reset();
      start.mutate(accountId, {
        onSuccess: (r) => {
          trackRun(r.run_id);
          onStarted?.();
        },
      });
    },
    track: trackRun,
    error: wrong ?? detail(submit.error),
    /** Failures to show apart from the code field: a failed resend, or a non-API error on submit. */
    actionError: startMessage(start.error) ?? (submit.error instanceof ApiError ? null : message(submit.error)),
    attempt,
    pending: submit.isPending,
    resending: start.isPending,
    authWaiting,
    sessionStillValid,
  };
}
