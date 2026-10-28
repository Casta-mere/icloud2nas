import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { useCachedSession, useSetup } from "../api/queries";
import { useT } from "../i18n";
import { guardImeSubmit } from "../lib/ime";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";

const INPUT = "w-full rounded-lg border-[1.5px] border-ink bg-white px-[9px] py-1.5 text-[13px]";
const SECTION = "flex flex-col gap-2 rounded-[10px] border-[1.5px] border-[#cbd5e1] bg-white p-3";

/** Shown instead of the app until someone chooses LAN or WAN mode (the API answers 428 meanwhile). */
export function Setup() {
  const t = useT();
  const session = useCachedSession();
  const lan = useSetup();
  const wan = useSetup();
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");

  const pending = lan.isPending || wan.isPending;
  const mismatch = again !== "" && again !== password;
  const canSubmit = password !== "" && again === password && !pending;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (canSubmit) wan.mutate({ mode: "wan", password });
  };

  const messageOf = (error: Error | null) =>
    error instanceof ApiError ? error.detail : error ? `${t("error-prefix")}${error.message}` : null;
  const lanMessage = messageOf(lan.error);
  const wanMessage = mismatch ? t("password-mismatch") : messageOf(wan.error);

  return (
    <div className="page-bg grid place-items-center px-4 py-6">
      <Card className="w-full max-w-[680px]">
        <p className="mb-0.5 text-lg font-extrabold tracking-[-.4px]">{t("setup-title")}</p>
        <p className="mb-3 font-mono text-xs text-[#6b7280]">{t("setup-sub")}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <section className={SECTION}>
            <h2 className="text-sm font-extrabold">{t("setup-lan-title")}</h2>
            <p className="text-xs text-[#6b7280]">{t("setup-lan-text")}</p>
            {session.data?.remote && <p className="text-xs text-[#9a3412]">{t("outside-note")}</p>}
            {lanMessage && (
              <p role="alert" className="text-xs text-[#9a3412]">
                {lanMessage}
              </p>
            )}
            <Button disabled={pending} onClick={() => lan.mutate({ mode: "lan" })} className="mt-auto w-full">
              {t("setup-lan-btn")}
            </Button>
          </section>
          <section className={SECTION}>
            <h2 className="text-sm font-extrabold">{t("setup-wan-title")}</h2>
            <p className="text-xs text-[#6b7280]">{t("setup-wan-text")}</p>
            <form className="flex flex-1 flex-col gap-2" onKeyDownCapture={guardImeSubmit} onSubmit={submit}>
              <input
                type="password"
                name="password"
                autoComplete="new-password"
                placeholder={t("login-password")}
                aria-label={t("login-password")}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={INPUT}
              />
              <input
                type="password"
                name="password-confirm"
                autoComplete="new-password"
                placeholder={t("confirm-password")}
                aria-label={t("confirm-password")}
                value={again}
                onChange={(e) => setAgain(e.target.value)}
                className={INPUT}
              />
              {wanMessage && (
                <p role="alert" className="text-xs text-[#9a3412]">
                  {wanMessage}
                </p>
              )}
              <Button type="submit" variant="primary" disabled={!canSubmit} className="mt-auto w-full">
                {t("setup-wan-btn")}
              </Button>
            </form>
          </section>
        </div>
      </Card>
    </div>
  );
}
