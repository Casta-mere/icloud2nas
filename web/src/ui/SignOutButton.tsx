import { useCachedSession, useLogout } from "../api/queries";
import { useT } from "../i18n";
import { Button } from "./Button";

/** 退出登录: only when an admin password is set and this browser is signed in. */
export function SignOutButton() {
  const t = useT();
  const session = useCachedSession();
  const logout = useLogout();
  if (!session.data?.auth_required || !session.data.authenticated) return null;
  return (
    <Button
      variant="ghost"
      disabled={logout.isPending}
      onClick={() => logout.mutate()}
      className="px-2.5 py-1 text-xs"
    >
      {t("sign-out")}
    </Button>
  );
}
