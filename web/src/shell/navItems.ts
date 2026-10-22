import type { Key } from "../i18n";

export const NAV_ITEMS: readonly { to: string; label: Key; end?: boolean }[] = [
  { to: "/", label: "nav-overview", end: true },
  { to: "/accounts", label: "nav-accounts" },
  { to: "/libraries", label: "nav-libraries" },
  { to: "/trash", label: "nav-trash" },
  { to: "/settings", label: "nav-settings" },
];
