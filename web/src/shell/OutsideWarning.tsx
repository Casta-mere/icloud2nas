import { useCachedSession } from "../api/queries";
import { useT } from "../i18n";

/** One-line warning strip: LAN mode (no password) reached from outside. No controls, so a phone's code input stays the first one. */
export function OutsideWarning() {
  const t = useT();
  const session = useCachedSession();
  if (session.data?.mode !== "lan" || !session.data.remote) return null;
  return (
    <p role="status" data-testid="outside-banner" className="mb-3 text-xs text-[#9a3412] sm:mb-[18px]">
      {t("outside-banner")}
    </p>
  );
}
