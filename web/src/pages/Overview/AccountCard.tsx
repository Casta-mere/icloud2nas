import { useState } from "react";
import { Link } from "react-router";
import type { Account, Library, Plan, Run } from "../../api/types";
import { useCodeFlow } from "../../events/useCodeFlow";
import { useT, type Key } from "../../i18n";
import { fmtTime } from "../../lib/format";
import { libraryChip } from "../../lib/status";
import { Button } from "../../ui/Button";
import { Card, type CardTone } from "../../ui/Card";
import { Chip, type ChipTone } from "../../ui/Chip";
import { CodeInput } from "../../ui/CodeInput";

const NEEDS_ATTENTION = ["awaiting_code", "needs_reauth", "password_rejected"];

function cardTone(status: Account["status"]): CardTone {
  if (NEEDS_ATTENTION.includes(status)) return "warn";
  return status === "ok" ? "ok" : "soft";
}

function headerChip(status: Account["status"]): { tone: ChipTone; key: Key } {
  switch (status) {
    case "awaiting_code":
    case "needs_reauth":
      return { tone: "warn", key: "status-awaiting-code" };
    case "password_rejected":
      return { tone: "fuse", key: "status-password-rejected" };
    case "ok":
      return { tone: "ok", key: "status-ok" };
    default:
      return { tone: "neutral", key: "status-new" };
  }
}

export function AccountCard({
  account,
  libraries,
  runs,
  plans = [],
}: {
  account: Account;
  libraries: Library[];
  runs: Run[];
  plans?: Plan[];
}) {
  const t = useT();
  const [forceCode, setForceCode] = useState(false);
  const flow = useCodeFlow(account.id, { onNeedCode: () => setForceCode(true) });

  const showCode =
    account.status === "awaiting_code" || (forceCode && account.status === "needs_reauth");
  const chip = headerChip(account.status);
  const resend = () => flow.resend();
  const reauth = () => {
    setForceCode(false);
    flow.resend();
  };

  const runningLibs = new Set(runs.filter((r) => r.status === "running").map((r) => r.library_id));
  const pendingLibs = new Set(plans.filter((p) => p.status === "pending").map((p) => p.library_id));

  return (
    <Card tone={cardTone(account.status)}>
      <article aria-label={account.display_name}>
        <h2 className="m-0 flex min-w-0 items-center gap-2 text-[22px] leading-tight font-extrabold">
          <span className="truncate">{account.display_name}</span>
          <Chip tone={chip.tone}>{t(chip.key)}</Chip>
        </h2>
        <p className="m-0 mt-0.5 truncate font-mono text-[12px] text-[#6b7280]">{account.apple_id}</p>

        {showCode ? (
          <div className="mt-3">
            <div className="text-[13px] text-[#374151]">{t("mfa-sent")}</div>
            <CodeInput
              onSubmit={flow.sendCode}
              error={flow.error}
              attempt={flow.attempt}
              pending={flow.pending}
            />
            {flow.authWaiting ? null : (
              <div className="mt-2 text-[12px] text-[#6b7280]">
                {t("code-no-receive")}
                <button
                  type="button"
                  onClick={resend}
                  disabled={flow.resending}
                  className="cursor-pointer border-0 bg-transparent p-0 text-[12px] whitespace-nowrap text-[#6b7280] underline"
                >
                  {t("code-resend")}
                </button>
              </div>
            )}
          </div>
        ) : null}

        {account.status === "needs_reauth" && !showCode ? (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="primary" onClick={reauth} disabled={flow.resending || flow.authWaiting}>
              {t("reauth")}
            </Button>
          </div>
        ) : null}

        {account.status === "password_rejected" ? (
          <div className="mt-3 text-[13px] text-[#374151]">
            <p className="m-0">{t("password-rejected-text")}</p>
            <Link to="/accounts" className="mt-1 inline-block font-bold underline">
              {t("password-rejected-link")}
            </Link>
          </div>
        ) : null}

        {flow.actionError ? (
          <div role="alert" className="mt-2 text-[12px] text-[#991b1b]">
            {flow.actionError}
          </div>
        ) : null}

        <div className="mt-3 grid gap-2">
          {libraries.map((lib) => {
            const c = libraryChip(lib, runningLibs.has(lib.id), pendingLibs.has(lib.id));
            const files = lib.stats.files;
            return (
              <div key={lib.id} className="grid grid-cols-[96px_minmax(0,1fr)] items-baseline gap-x-2">
                <b className="truncate text-[13px]">{lib.display_name}</b>
                <div className="min-w-0">
                  <div>
                    <Chip tone={c.tone}>{t(c.key)}</Chip>
                  </div>
                  <div className="truncate text-[12px] text-[#4b5563] tabular-nums">
                    <span className="font-mono">{`/photos/${lib.target_dir}`}</span>
                    {typeof files === "number" ? ` · ${t("files-count", { n: files })}` : ""}
                    {" · "}
                    {lib.last_success_at
                      ? t("last-success", { time: fmtTime(lib.last_success_at) })
                      : t("never-synced")}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </article>
    </Card>
  );
}
