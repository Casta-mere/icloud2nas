import { Link } from "react-router";
import { useAccounts, useLibraries, usePlans } from "../../api/queries";
import type { Plan } from "../../api/types";
import { useT } from "../../i18n";
import { Empty } from "../../ui/Empty";
import { QueryState } from "../../ui/QueryState";
import { LibraryCard } from "./LibraryCard";

export function Libraries() {
  const t = useT();
  const libraries = useLibraries();
  const accounts = useAccounts();
  const plans = usePlans("all");

  if (!libraries.data || !accounts.data) {
    return <QueryState queries={[libraries, accounts]} />;
  }
  const personal = libraries.data.filter((l) => l.kind === "personal");
  if (personal.length === 0) {
    return (
      <div className="text-center">
        <Empty text={t("libraries-empty")} />
        <Link to="/accounts" className="font-bold underline">
          {t("overview-empty-link")}
        </Link>
      </div>
    );
  }

  // pending = awaiting a decision, approved = being applied; the newest per library
  const open = new Map<string, Plan>();
  for (const p of [...(plans.data ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at))) {
    if (p.status === "pending" || p.status === "approved") open.set(p.library_id, p);
  }

  return (
    <div className="grid grid-cols-1 gap-4 min-[960px]:grid-cols-2">
      {personal.map((library) => {
        const account = accounts.data.find((a) => a.id === library.account_id);
        return account ? (
          <LibraryCard key={library.id} library={library} account={account} plan={open.get(library.id)} />
        ) : null;
      })}
    </div>
  );
}
