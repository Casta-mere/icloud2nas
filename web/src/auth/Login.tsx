import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { useLogin } from "../api/queries";
import { isImeEnter } from "../lib/ime";
import { useT } from "../i18n";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";

/** Shown instead of the app when an admin password is set and there is no valid session. */
export function Login() {
  const t = useT();
  const login = useLogin();
  const [password, setPassword] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ password });
  };

  const error = login.error;
  const message =
    error instanceof ApiError ? error.detail : error ? `${t("error-prefix")}${error.message}` : null;

  return (
    <div className="page-bg grid place-items-center px-4">
      <Card className="w-full max-w-[340px]">
        <p className="mb-0.5 text-lg font-extrabold tracking-[-.4px]">{t("brand")}</p>
        <p className="mb-3 font-mono text-xs text-[#6b7280]">{t("login-sub")}</p>
        <form onSubmit={submit}>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            placeholder={t("login-password")}
            aria-label={t("login-password")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (isImeEnter(e)) e.preventDefault();
            }}
            className="w-full rounded-lg border-[1.5px] border-ink bg-white px-[9px] py-1.5 text-[13px]"
          />
          {message && (
            <p role="alert" className="mt-1 text-xs text-[#9a3412]">
              {message}
            </p>
          )}
          <Button type="submit" variant="primary" disabled={login.isPending} className="mt-3 w-full">
            {t("login-submit")}
          </Button>
        </form>
        <p className="mt-2.5 text-xs text-[#6b7280]">
          {t("login-forgot")} <span className="font-mono">{"icloud2nas-serve --reset-admin-password"}</span>
        </p>
      </Card>
    </div>
  );
}
