import { usePauseAll, useResumeAll, useStatus } from "../api/queries";
import { useStream } from "../events/StreamContext";
import { useT } from "../i18n";
import { Button } from "../ui/Button";
import { LangSwitch } from "../ui/LangSwitch";
import { SignOutButton } from "../ui/SignOutButton";

/** The compact prompt line for phones (under 640px): connection, 全局暂停/继续, language, 退出登录. */
export function PhoneBar() {
  const t = useT();
  const { connected } = useStream();
  const status = useStatus();
  const pause = usePauseAll();
  const resume = useResumeAll();
  const paused = status.data?.paused ?? false;
  const toggle = paused ? resume : pause;

  return (
    <div data-testid="phone-bar" className="mb-3 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 sm:hidden">
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
        variant="ghost"
        disabled={toggle.isPending || !status.data}
        onClick={() => toggle.mutate()}
        className="px-2.5 py-1 text-xs"
      >
        {paused ? t("resume-all") : t("pause-all")}
      </Button>
      <span className="ml-auto inline-flex items-center gap-2">
        <LangSwitch />
        <SignOutButton />
      </span>
    </div>
  );
}
