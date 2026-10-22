import { ApiError } from "../api/client";
import { useAccounts, usePauseAll, useResumeAll, useStatus } from "../api/queries";
import { useStream } from "../events/StreamContext";
import { useT } from "../i18n";
import { Button } from "./Button";
import { LangSwitch } from "./LangSwitch";
import { SignOutButton } from "./SignOutButton";

/** The shell's pseudo-command line: counts, stream state, 全局暂停/继续, language, 退出登录 (desktop only). */
export function PromptBar({ className = "" }: { className?: string }) {
  const t = useT();
  const { connected } = useStream();
  const accounts = useAccounts();
  const status = useStatus();
  const pause = usePauseAll();
  const resume = useResumeAll();

  const nAccounts = accounts.data?.length;
  const nWorkers = status.data?.workers_running;
  const count = (n: number | undefined, one: "prompt-accounts-one" | "prompt-workers-one", other: "prompt-accounts-other" | "prompt-workers-other") =>
    t(n === 1 ? one : other, { n: n ?? "–" });
  const paused = status.data?.paused ?? false;
  const toggle = paused ? resume : pause;

  return (
    <div data-testid="prompt-bar" className={`flex min-w-0 flex-wrap items-center gap-3 ${className}`}>
      <div className="min-w-0 truncate rounded-lg border-[1.5px] border-ink bg-[rgba(255,255,255,.55)] px-2.5 py-[5px] font-mono text-xs text-[#374151] tabular-nums shadow-[3px_3px_0_#111]">
        {t("prompt-status", {
          accounts: count(nAccounts, "prompt-accounts-one", "prompt-accounts-other"),
          workers: count(nWorkers, "prompt-workers-one", "prompt-workers-other"),
        })}
      </div>
      <span
        role="status"
        className={`inline-flex items-center gap-1.5 text-xs whitespace-nowrap ${connected ? "text-[#166534]" : "text-[#9a3412]"}`}
      >
        <span
          aria-hidden="true"
          className={`inline-block h-2 w-2 rounded-full ${connected ? "bg-[#16a34a]" : "bg-[#ea580c]"}`}
        />
        {connected ? t("conn-live") : t("conn-lost")}
      </span>
      {paused && (
        <span className="rounded-full bg-[rgba(234,88,12,.12)] px-2 py-0.5 text-[11px] whitespace-nowrap text-[#9a3412]">
          {t("paused-state")}
        </span>
      )}
      <Button
        variant={paused ? "primary" : "ghost"}
        disabled={toggle.isPending || !status.data}
        onClick={() => toggle.mutate()}
        className="px-2.5 py-1 text-xs"
      >
        {paused ? t("resume-all") : t("pause-all")}
      </Button>
      {toggle.error && (
        <span role="alert" className="text-xs text-[#9a3412]">
          {toggle.error instanceof ApiError
            ? toggle.error.detail
            : `${t("error-prefix")}${toggle.error.message}`}
        </span>
      )}
      <LangSwitch />
      <SignOutButton />
    </div>
  );
}
