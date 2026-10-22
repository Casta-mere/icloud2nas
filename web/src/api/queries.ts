import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { request, requestText } from "./client";
import type {
  Account,
  Library,
  LibraryPatch,
  NewAccount,
  Plan,
  PlanDetail,
  PlanStatus,
  Run,
  RunFilters,
  Session,
  SettingsResponse,
  SettingsUpdate,
  Status,
  TrashEntry,
  TrashFile,
} from "./types";

export type { SettingsKey } from "./types";

export const keys = {
  accounts: ["accounts"] as const,
  libraries: ["libraries"] as const,
  runsAll: ["runs"] as const,
  runs: (filters: RunFilters = {}) => ["runs", filters] as const,
  runLog: (id: string) => ["runs", "log", id] as const,
  plansAll: ["plans"] as const,
  plans: (status: PlanStatus | "all" = "pending") => ["plans", status] as const,
  plan: (id: string) => ["plans", "detail", id] as const,
  trash: ["trash"] as const,
  trashDay: (day: string, rel: string) => ["trash", day, rel] as const,
  settings: ["settings"] as const,
  status: ["status"] as const,
  session: ["session"] as const,
};

const enc = encodeURIComponent;

function query(params: Record<string, string | number | boolean | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined) q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export function invalidate(qc: QueryClient, queryKeys: readonly QueryKey[]): Promise<void> {
  return Promise.all(queryKeys.map((queryKey) => qc.invalidateQueries({ queryKey }))).then(
    () => undefined,
  );
}

// ---- queries -------------------------------------------------------------------------------

export const useSession = () =>
  useQuery({ queryKey: keys.session, queryFn: () => request<Session>("GET", "/api/session") });

/** The session App's gate already loaded, read without fetching again (one GET per page load). */
export const useCachedSession = () =>
  useQuery({
    queryKey: keys.session,
    queryFn: () => request<Session>("GET", "/api/session"),
    enabled: false,
  });

export const useStatus = () =>
  useQuery({ queryKey: keys.status, queryFn: () => request<Status>("GET", "/api/status") });

export const useAccounts = () =>
  useQuery({ queryKey: keys.accounts, queryFn: () => request<Account[]>("GET", "/api/accounts") });

export const useLibraries = () =>
  useQuery({ queryKey: keys.libraries, queryFn: () => request<Library[]>("GET", "/api/libraries") });

export const useRuns = (filters: RunFilters = {}) =>
  useQuery({
    queryKey: keys.runs(filters),
    queryFn: () => request<Run[]>("GET", `/api/runs${query({ ...filters })}`),
  });

export const useRunLog = (id: string, opts: { refetchInterval: number | false }) =>
  useQuery({
    queryKey: keys.runLog(id),
    queryFn: () => requestText(`/api/runs/${enc(id)}/log`),
    refetchInterval: opts.refetchInterval,
  });

export const usePlans = (status: PlanStatus | "all" = "pending") =>
  useQuery({
    queryKey: keys.plans(status),
    queryFn: () => request<Plan[]>("GET", `/api/plans${query({ status })}`),
  });

export const usePlanDetail = (id: string) =>
  useQuery({
    queryKey: keys.plan(id),
    queryFn: () => request<PlanDetail>("GET", `/api/plans/${enc(id)}`),
  });

export const useTrash = () =>
  useQuery({ queryKey: keys.trash, queryFn: () => request<TrashEntry[]>("GET", "/api/trash") });

export const useTrashDay = (day: string, rel: string) =>
  useQuery({
    queryKey: keys.trashDay(day, rel),
    queryFn: () => request<TrashFile[]>("GET", `/api/trash/${enc(day)}${query({ rel })}`),
  });

export const useSettings = () =>
  useQuery({
    queryKey: keys.settings,
    queryFn: () => request<SettingsResponse>("GET", "/api/settings"),
  });

// ---- mutations -----------------------------------------------------------------------------

/** A mutation that invalidates what its SSE topic would, so the UI is right without the event. */
function useAction<TVars, TResult>(
  fn: (vars: TVars) => Promise<TResult>,
  affects: readonly QueryKey[] | ((vars: TVars) => readonly QueryKey[]),
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (_data, vars) => invalidate(qc, typeof affects === "function" ? affects(vars) : affects),
  });
}

export const useCreateAccount = () =>
  useAction(
    (body: NewAccount) => request<{ id: string; run_id: string }>("POST", "/api/accounts", body),
    [keys.accounts, keys.libraries, keys.runsAll, keys.status],
  );

export const usePatchAccount = () =>
  useAction(
    ({ id, ...body }: { id: string; display_name?: string; password?: string }) =>
      request<Account & { run_id?: string }>("PATCH", `/api/accounts/${enc(id)}`, body),
    (vars) => (vars.password ? [keys.accounts, keys.runsAll, keys.status] : [keys.accounts]),
  );

export const useDeleteAccount = () =>
  useAction(
    ({ id, keepDir }: { id: string; keepDir: boolean }) =>
      request<undefined>("DELETE", `/api/accounts/${enc(id)}${query({ keep_dir: keepDir })}`),
    [keys.accounts, keys.libraries, keys.runsAll, keys.plansAll, keys.trash],
  );

export const useStartAuth = () =>
  useAction(
    (id: string) => request<{ run_id: string }>("POST", `/api/accounts/${enc(id)}/auth`),
    [keys.accounts, keys.runsAll, keys.status],
  );

export const useSubmitCode = () =>
  useAction(
    ({ id, code }: { id: string; code: string }) =>
      request<undefined>("POST", `/api/accounts/${enc(id)}/code`, { code }),
    [keys.accounts],
  );

export const useRefreshAccount = () =>
  useAction(
    (id: string) =>
      request<{ personal: string[]; shared: string[] }>("POST", `/api/accounts/${enc(id)}/refresh`),
    [keys.accounts, keys.libraries],
  );

export const usePatchLibrary = () =>
  useAction(
    ({ id, ...body }: LibraryPatch & { id: string }) =>
      request<Library>("PATCH", `/api/libraries/${enc(id)}`, body),
    [keys.libraries, keys.trash],
  );

export const useRequestRun = () =>
  useAction(
    ({ id, mode }: { id: string; mode: "sync" | "reconcile" }) =>
      request<{ queued: boolean }>("POST", `/api/libraries/${enc(id)}/${mode}`),
    [keys.runsAll, keys.status, keys.libraries],
  );

export const usePauseLibrary = () =>
  useAction(
    (id: string) => request<Library>("POST", `/api/libraries/${enc(id)}/pause`),
    [keys.libraries],
  );

export const useResumeLibrary = () =>
  useAction(
    (id: string) => request<Library>("POST", `/api/libraries/${enc(id)}/resume`),
    [keys.libraries],
  );

export const useApprovePlan = () =>
  useAction(
    (id: string) => request<Plan>("POST", `/api/plans/${enc(id)}/approve`),
    [keys.plansAll, keys.libraries, keys.runsAll, keys.status],
  );

export const useRejectPlan = () =>
  useAction(
    (id: string) => request<Plan>("POST", `/api/plans/${enc(id)}/reject`),
    [keys.plansAll, keys.libraries],
  );

export const useKeepFiles = () =>
  useAction(
    ({ day, rel, paths }: { day: string; rel: string; paths: string[] }) =>
      request<{ kept: string[] }>("POST", `/api/trash/${enc(day)}/keep${query({ rel })}`, { paths }),
    [keys.trash],
  );

export const useDeleteDay = () =>
  useAction(
    ({ day, rel }: { day: string; rel: string }) =>
      request<{ deleted: number }>("DELETE", `/api/trash/${enc(day)}${query({ rel })}`),
    [keys.trash],
  );

export const usePutSettings = () =>
  useAction(
    (body: SettingsUpdate) => request<SettingsResponse>("PUT", "/api/settings", body),
    (vars) =>
      "admin_password" in vars
        ? [keys.settings, keys.status, keys.session]
        : [keys.settings, keys.status],
  );

/**
 * Sets (or, with "", clears) the admin password. The server issues no cookie on set, so the login
 * happens inside the mutation, before the session refetch can unmount the caller.
 */
export const useSetAdminPassword = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (password: string): Promise<{ loginFailed: boolean }> => {
      await request<SettingsResponse>("PUT", "/api/settings", { admin_password: password });
      if (password === "") return { loginFailed: false };
      try {
        await request<undefined>("POST", "/api/session", { password });
        return { loginFailed: false };
      } catch {
        return { loginFailed: true };
      }
    },
    onSettled: () => invalidate(qc, [keys.settings, keys.status, keys.session]),
  });
};

export const useNotifyTest = () =>
  useAction<void, { sent: boolean }>(() => request<{ sent: boolean }>("POST", "/api/notify/test"), []);

export const usePauseAll = () =>
  useAction<void, { paused: boolean }>(
    () => request<{ paused: boolean }>("POST", "/api/pause"),
    [keys.status, keys.libraries, keys.settings],
  );

export const useResumeAll = () =>
  useAction<void, { paused: boolean }>(
    () => request<{ paused: boolean }>("POST", "/api/resume"),
    [keys.status, keys.libraries, keys.settings],
  );

export const useLogin = () =>
  useAction(
    ({ password }: { password: string }) => request<undefined>("POST", "/api/session", { password }),
    [keys.session],
  );

/** Sign out: show the login card and drop every other cached resource (no session refetch needed). */
export const useLogout = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => request<undefined>("DELETE", "/api/session"),
    onSuccess: () => {
      // the session query stays (its observer is the gate in App); everything else goes, as clear() would
      qc.setQueryData<Session>(keys.session, { auth_required: true, authenticated: false });
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== keys.session[0] });
      qc.getMutationCache().clear();
    },
  });
};
