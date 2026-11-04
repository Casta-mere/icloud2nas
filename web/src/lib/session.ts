import type { Account } from "../api/types";
import type { Key } from "../i18n";

const DAY_MS = 24 * 60 * 60 * 1000;
/** The server warns at 7 days and at 1 day; the card turns to the warning tone from 7 days on. */
export const SESSION_WARN_DAYS = 7;

export interface SessionExpiry {
  key: Key;
  /** Whole days left, rounded up (a session lapsing in 2 h still has "1 day"). */
  days: number;
  warn: boolean;
}

/** "会话 N 天后到期" for an account whose expiry is known; null when it is not. */
export function sessionExpiry(account: Account, now: number = Date.now()): SessionExpiry | null {
  if (!account.session_expires_at) return null;
  const at = new Date(account.session_expires_at).getTime();
  if (Number.isNaN(at)) return null;
  const days = Math.ceil((at - now) / DAY_MS);
  if (days <= 0) return { key: "session-expired", days: 0, warn: true };
  const about = account.session_expiry_estimated;
  const key: Key =
    days === 1
      ? about
        ? "session-expires-about-one"
        : "session-expires-in-one"
      : about
        ? "session-expires-about"
        : "session-expires-in";
  return { key, days, warn: days <= SESSION_WARN_DAYS };
}
