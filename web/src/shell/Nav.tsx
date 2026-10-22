import { NavLink } from "react-router";
import { useT } from "../i18n";
import { NAV_ITEMS } from "./navItems";

/** Desktop top nav; hidden below 640px (the bottom tabs take over). */
export function Nav() {
  const t = useT();
  return (
    <nav aria-label={t("nav-main")} className="flex gap-3.5 text-[13px] text-[#4b5563] max-sm:hidden">
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) =>
            `border-b-2 pb-0.5 whitespace-nowrap ${isActive ? "border-ink font-bold text-ink" : "border-transparent hover:text-ink"}`
          }
        >
          {t(item.label)}
        </NavLink>
      ))}
    </nav>
  );
}
