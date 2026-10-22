import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router";
import { onUnauthorized } from "./api/client";
import { keys, useSession } from "./api/queries";
import type { Session } from "./api/types";
import { Login } from "./auth/Login";
import { StreamProvider } from "./events/StreamProvider";
import { useT } from "./i18n";
import { Accounts } from "./pages/Accounts/Accounts";
import { Libraries } from "./pages/Libraries/Libraries";
import { Overview } from "./pages/Overview/Overview";
import { Settings } from "./pages/Settings/Settings";
import { Trash } from "./pages/Trash/Trash";
import { Shell } from "./shell/Shell";
import { Empty } from "./ui/Empty";

const LOGGED_OUT: Session = { auth_required: true, authenticated: false };

/** Session gate + routes; the router itself comes from `main.tsx` (or the test's MemoryRouter). */
export default function App() {
  const t = useT();
  const qc = useQueryClient();
  const session = useSession();

  // any 401 drops back to the login card without refetching the session
  useEffect(() => onUnauthorized(() => qc.setQueryData(keys.session, LOGGED_OUT)), [qc]);

  if (session.isError && !session.data) {
    return (
      <div className="page-bg grid place-items-center">
        <Empty text={`${t("error-prefix")}${session.error.message}`} />
      </div>
    );
  }
  if (!session.data) return <div className="page-bg" />;
  if (session.data.auth_required && !session.data.authenticated) return <Login />;

  return (
    <StreamProvider>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Overview />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="libraries" element={<Libraries />} />
          <Route path="trash" element={<Trash />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </StreamProvider>
  );
}
