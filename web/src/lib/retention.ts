import { useSettings } from "../api/queries";
import { useT } from "../i18n";

/** How long the trash keeps files, from `trash_retention_days` (0 = never purged automatically). */
export function useRetentionText(): string {
  const t = useT();
  const days = useSettings().data?.values.trash_retention_days;
  if (days === 0) return t("retention-forever");
  return t("retention-days", { days: days ?? "…" });
}
