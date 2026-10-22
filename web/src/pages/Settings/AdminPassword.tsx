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
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const fail = (e: Error) => setError(e instanceof ApiError ? e.detail : e.message);

  const set = () => {
    setError("");
    put.mutate(pw, {
      onSuccess: ({ loginFailed }) => {
        setPw("");
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

  return (
    <>
      <FormRow
        label={t("field-admin-password")}
        alignTop
        htmlFor="admin-password"
        hint={isSet ? t("admin-password-set") : t("admin-password-unset")}
        error={error}
      >
        <div className="grid gap-2">
          <input
            id="admin-password"
            type="password"
            autoComplete="new-password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            className={INPUT}
          />
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" disabled={pw === "" || put.isPending} onClick={set}>
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
