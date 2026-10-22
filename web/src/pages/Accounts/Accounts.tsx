import { useState } from "react";
import { useAccounts } from "../../api/queries";
import type { Account } from "../../api/types";
import { useT } from "../../i18n";
import { Card } from "../../ui/Card";
import { AccountRow } from "./AccountRow";
import { AddAccountWizard } from "./AddAccountWizard";
import { DeleteAccountDialog } from "./DeleteAccountDialog";

export function Accounts() {
  const t = useT();
  const accounts = useAccounts();
  const [wizardKey, setWizardKey] = useState(0);
  const [deleting, setDeleting] = useState<Account | null>(null);

  return (
    <div className="grid items-start gap-4 md:grid-cols-2">
      <AddAccountWizard key={wizardKey} onDone={() => setWizardKey((k) => k + 1)} />
      <div className="grid min-w-0 gap-4">
        <Card>
          <h2 className="m-0 mb-1 text-lg font-extrabold">{t("existing-accounts")}</h2>
          {(accounts.data ?? []).map((a) => (
            <AccountRow key={a.id} account={a} onDelete={setDeleting} />
          ))}
        </Card>
        {deleting ? (
          <DeleteAccountDialog key={deleting.id} account={deleting} onClose={() => setDeleting(null)} />
        ) : null}
      </div>
    </div>
  );
}
