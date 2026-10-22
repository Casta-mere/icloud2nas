import type { Account, Library } from "../api/types";
import { t } from "../i18n";

function nameOf(items: readonly { id: string; display_name: string }[] | undefined, id: string | null): string {
  if (!id) return "—";
  const found = items?.find((item) => item.id === id);
  return found ? found.display_name : `${id.slice(0, 8)} · ${t("deleted")}`;
}

/** A library's display name, or `<id8> · 已删除` when it no longer exists. */
export const libraryName = (libs: readonly Library[] | undefined, id: string | null) => nameOf(libs, id);

/** An account's display name, or `<id8> · 已删除` when it no longer exists. */
export const accountName = (accounts: readonly Account[] | undefined, id: string | null) =>
  nameOf(accounts, id);
