import { useState } from "react";
import { ApiError } from "../../api/client";
import { useSetAdminPassword } from "../../api/queries";
import { useT } from "../../i18n";
import { Button } from "../../ui/Button";
import { Confirm } from "../../ui/Confirm";
import { FormRow } from "../../ui/FormRow";

const INPUT = "w-full rounded-[10px] border-2 border-ink bg-white px-2.5 py-1.5 text-[13px]";

/** Set / clear the admin password; PUTs on its own, separate from the save bar. */
export function AdminPassword({ isSet }: { isSet: boolean }) {
  const t = useT();
  const put = useSetAdminPassword();
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const fail = (e: Error) => setError(e instanceof ApiError ? e.detail : e.message);

  const set = () => {
    setError("");
    put.mutate(pw, {
      onSuccess: ({ loginFailed }) => {
        setPw("");
        setAgain("");
        if (loginFailed) setError(t("password-set-login-failed"));
      },
      onError: fail,
    });
  };
  const clear = () => {
    setConfirming(false);
    setError("");
    put.mutate("", { onError: fail });
  };

  const mismatch = again !== "" && again !== pw;
  const canSet = pw !== "" && again === pw && !put.isPending;

  return (
    <>
      <FormRow
        label={t("field-admin-password")}
        htmlFor="admin-password"
        hint={isSet ? t("admin-password-set") : t("admin-password-unset")}
      >
        <input
          id="admin-password"
          type="password"
          autoComplete="new-password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          className={INPUT}
        />
      </FormRow>
      <FormRow
        label={t("confirm-password")}
        alignTop
        htmlFor="admin-password-again"
        error={mismatch ? t("password-mismatch") : error}
      >
        <div className="grid gap-2">
          <input
            id="admin-password-again"
            type="password"
            autoComplete="new-password"
            value={again}
            onChange={(e) => setAgain(e.target.value)}
            className={INPUT}
          />
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" disabled={!canSet} onClick={set}>
              {t("admin-password-set-btn")}
            </Button>
            {isSet && (
              <Button variant="danger" disabled={put.isPending} onClick={() => setConfirming(true)}>
                {t("admin-password-clear")}
              </Button>
            )}
          </div>
        </div>
      </FormRow>
      <Confirm
        open={confirming}
        danger
        title={t("admin-password-clear-title")}
        body={t("admin-password-clear-body")}
        confirmLabel={t("admin-password-clear")}
        onConfirm={clear}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}
