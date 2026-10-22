import { useState } from "react";
import { Link } from "react-router";
import { useAccounts, useLibraries, usePlans, useRuns } from "../../api/queries";
import type { Account } from "../../api/types";
import { useT } from "../../i18n";
import { Empty } from "../../ui/Empty";
import { AccountCard } from "./AccountCard";
import { LogDrawer } from "./LogDrawer";
import { RecentRuns } from "./RecentRuns";

const RANK: Partial<Record<Account["status"], number>> = {
  awaiting_code: 0,
  needs_reauth: 1,
  password_rejected: 2,
};
const rank = (a: Account) => RANK[a.status] ?? 3;

export function Overview() {
  const t = useT();
  const accounts = useAccounts();
  const libraries = useLibraries();
  const recent = useRuns({ limit: 20 });
  const active = useRuns({ limit: 50 });
  const plans = usePlans();
  const [openId, setOpenId] = useState<string | null>(null);

  if (!accounts.data) return null;
  if (accounts.data.length === 0) {
    return (
      <div className="text-center">
        <Empty text={t("overview-empty")} />
        <Link to="/accounts" className="font-bold underline">
          {t("overview-empty-link")}
        </Link>
      </div>
    );
  }

  const sorted = [...accounts.data].sort(
    (a, b) => rank(a) - rank(b) || a.display_name.localeCompare(b.display_name),
  );
  const libs = libraries.data ?? [];
  const recentRuns = recent.data ?? [];
  const openRun = openId ? [...recentRuns, ...(active.data ?? [])].find((r) => r.id === openId) : undefined;

  return (
    <div className="grid grid-cols-1 items-start gap-4 min-[960px]:grid-cols-[1.1fr_.9fr]">
      <div className="grid content-start gap-4">
        {sorted.map((account) => (
          <AccountCard
            key={account.id}
            account={account}
            libraries={libs.filter((l) => l.account_id === account.id)}
            runs={active.data ?? []}
            plans={plans.data ?? []}
          />
        ))}
      </div>
      <div className="min-w-0">
        <RecentRuns
          runs={recentRuns}
          libraries={libs}
          accounts={accounts.data}
          onOpen={(run) => setOpenId(run.id)}
        />
      </div>
      {openRun ? <LogDrawer run={openRun} onClose={() => setOpenId(null)} /> : null}
    </div>
  );
}
