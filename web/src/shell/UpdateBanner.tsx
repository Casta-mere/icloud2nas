import { useState } from "react";
import { Link } from "react-router";
import { useAccounts, useStatus } from "../api/queries";
import { useT } from "../i18n";

/** `localStorage` key holding the release whose banner was closed in this browser. */
export const DISMISSED_KEY = "i2n.update-dismissed";

function readDismissed(): string | null {
  try {
    return localStorage.getItem(DISMISSED_KEY);
  } catch {
    return null;
  }
}

const LINK = "font-bold whitespace-nowrap underline underline-offset-2";
const HOW_TO = "/settings#about";

/**
 * The full-width "a newer release is out" strip above the header, on every page of the shell.
 * ✕ hides it for that release in this browser; a newer one brings it back. It steps aside at every
 * width while an account waits for a code: on phones the code input stays the first control
 * (docs/web.md rule 7), and the code is urgent while the update can wait.
 */
export function UpdateBanner() {
  const t = useT();
  const status = useStatus();
  const accounts = useAccounts();
  const [dismissed, setDismissed] = useState(readDismissed);

  const update = status.data?.update;
  const latest = update?.update_available ? update.latest : null;
  // until the accounts are known, a waiting code can't be ruled out
  const codeWaiting = accounts.data?.some((a) => a.status === "awaiting_code") ?? true;
  if (!latest || latest === dismissed || codeWaiting) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(DISMISSED_KEY, latest);
    } catch {
      // not persisted: hidden for this visit only
    }
    setDismissed(latest);
  };

  return (
    <div data-testid="update-banner" className="update-stripes border-b-2 border-ink text-[13px] text-ink">
      <div className="mx-auto flex max-w-[1180px] items-center gap-2 px-3.5 py-2 sm:px-[26px]">
        {/* balances the ✕ so the desktop line sits in the middle of the page */}
        <span aria-hidden="true" className="w-7 shrink-0 max-sm:hidden" />
        <p className="m-0 flex min-w-0 flex-1 flex-wrap items-center justify-center gap-x-2.5 gap-y-1 max-sm:hidden">
          <span className="font-bold">{t("banner-update", { brand: t("brand"), latest })}</span>
          {update?.compare_url && (
            <>
              <a href={update.compare_url} target="_blank" rel="noreferrer" className={LINK}>
                {t("update-whats-new")}
              </a>
              <span aria-hidden="true">{"·"}</span>
            </>
          )}
          <Link to={HOW_TO} className={LINK}>
            {t("banner-how-to")}
          </Link>
        </p>
        <p className="m-0 flex min-w-0 flex-1 items-center gap-x-2 sm:hidden">
          <span className="min-w-0 truncate font-bold">{t("banner-update-short", { latest })}</span>
          <span aria-hidden="true">{"·"}</span>
          <Link to={HOW_TO} className={LINK}>
            {t("banner-update-link")}
          </Link>
        </p>
        <button
          type="button"
          aria-label={t("banner-dismiss")}
          onClick={dismiss}
          className="w-7 shrink-0 cursor-pointer rounded-md py-0.5 text-base leading-none hover:bg-[rgba(17,17,17,.08)]"
        >
          <span aria-hidden="true">{"✕"}</span>
        </button>
      </div>
    </div>
  );
}
