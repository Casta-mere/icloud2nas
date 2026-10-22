import { Outlet } from "react-router";
import { useT } from "../i18n";
import { PromptBar } from "../ui/PromptBar";
import { BottomTabs } from "./BottomTabs";
import { Nav } from "./Nav";
import { PhoneBar } from "./PhoneBar";

export function Shell() {
  const t = useT();
  return (
    <div className="page-bg">
      <div className="mx-auto max-w-[1180px] px-3.5 pt-4 pb-24 sm:px-[26px] sm:pt-[22px] sm:pb-7">
        <header className="mb-3 flex min-w-0 flex-wrap items-baseline gap-x-[18px] gap-y-2 sm:mb-[18px]">
          <div className="text-lg font-extrabold tracking-[-.3px]">{t("brand")}</div>
          <Nav />
          <PromptBar className="ml-auto max-sm:hidden" />
        </header>
        <PhoneBar />
        <main>
          <Outlet />
        </main>
      </div>
      <BottomTabs />
    </div>
  );
}
