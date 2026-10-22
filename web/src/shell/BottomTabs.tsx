import { NavLink } from "react-router";
import { useT } from "../i18n";
import { NAV_ITEMS } from "./navItems";

/** Phone tab bar (≤ 640px only, rule 7); rendered after the page content in the DOM. */
export function BottomTabs() {
  const t = useT();
  return (
    <nav
      aria-label={t("nav-bottom")}
      className="fixed inset-x-0 bottom-0 z-30 flex justify-around border-t-2 border-ink bg-[rgba(255,255,255,.7)] pt-2.5 pb-[max(12px,env(safe-area-inset-bottom))] text-[11px] text-[#4b5563] backdrop-blur-[10px] sm:hidden"
    >
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) => `px-2 whitespace-nowrap ${isActive ? "font-bold text-ink" : ""}`}
        >
          {t(item.label)}
        </NavLink>
      ))}
    </nav>
  );
}
