import { useState } from "react";
import { ApiError } from "../../api/client";
import { useDeleteAccount } from "../../api/queries";
import type { Account } from "../../api/types";
import { useT } from "../../i18n";
import { useRetentionText } from "../../lib/retention";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";

export function DeleteAccountDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const t = useT();
  const del = useDeleteAccount();
  const [keepDir, setKeepDir] = useState(true);
  const retention = useRetentionText();
  const title = t("delete-title", { name: account.display_name });

  return (
    <Card tone="fuse">
      <section aria-label={title}>
        <h3 className="m-0 mb-2 truncate text-base font-extrabold">{title}</h3>
        <p className="m-0 mb-2.5 text-[13px] text-[#374151]">{t("delete-body")}</p>
        <div className="grid gap-1.5 text-[13px]">
          <label className="flex items-center gap-2">
            <input type="radio" name="keep-dir" checked={keepDir} onChange={() => setKeepDir(true)} />
            {t("delete-keep", { dir: account.primary_dir ?? "" })}
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" name="keep-dir" checked={!keepDir} onChange={() => setKeepDir(false)} />
            {t("delete-trash", { retention })}
          </label>
        </div>
        {del.error ? (
          <div role="alert" className="mt-2 text-[12px] text-[#991b1b]">
            {del.error instanceof ApiError ? del.error.detail : del.error.message}
          </div>
        ) : null}
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t("cancel")}
          </Button>
          <Button
            variant="danger"
            disabled={del.isPending}
            onClick={() => del.mutate({ id: account.id, keepDir }, { onSuccess: onClose })}
          >
            {t("delete-confirm")}
          </Button>
        </div>
      </section>
    </Card>
  );
}
