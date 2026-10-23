import { http, HttpResponse, type HttpResponseResolver } from "msw";
import { setupServer } from "msw/node";
import type {
  Account,
  HostUser,
  Library,
  Plan,
  PlanDetail,
  Run,
  SettingsResponse,
  SettingsValues,
  Status,
  TrashEntry,
  TrashFile,
} from "../api/types";

export const ids = {
  xugang: "a1c0ffee000000000000000000000001",
  huanyijing: "a2c0ffee000000000000000000000002",
  libXugang: "b1d00d00000000000000000000000001",
  libHuanyijing: "b2d00d00000000000000000000000002",
  plan: "c1fa11ed000000000000000000000001",
  runs: [
    "d1000000000000000000000000000001",
    "d2000000000000000000000000000002",
    "d3000000000000000000000000000003",
    "d4000000000000000000000000000004",
  ] as const,
};

/** The admin password the fixture server accepts when auth is required. */
export const ADMIN_PASSWORD = "hunter2";

const accounts = (): Account[] => [
  {
    id: ids.xugang,
    display_name: "旭刚",
    apple_id: "xugang@example.com",
    region: "cn",
    status: "ok",
    failures: 0,
    backoff_until: null,
    last_auth_ok_at: "2026-10-21T09:14:00+00:00",
    last_error: null,
    created_at: "2026-10-14T02:00:00+00:00",
    primary_dir: "旭刚",
    library_ids: [ids.libXugang],
    owner_uid: 1000,
    owner_gid: 10,
    owner_name: "Castamere",
  },
  {
    id: ids.huanyijing,
    display_name: "幻伊镜",
    apple_id: "huanyijing@icloud.com",
    region: "cn",
    status: "awaiting_code",
    failures: 0,
    backoff_until: null,
    last_auth_ok_at: null,
    last_error: null,
    created_at: "2026-10-21T12:00:00+00:00",
    primary_dir: "幻伊镜",
    library_ids: [ids.libHuanyijing],
    owner_uid: 1001,
    owner_gid: 10,
    owner_name: "Today_red",
  },
];

const libraries = (): Library[] => [
  {
    id: ids.libXugang,
    account_id: ids.xugang,
    kind: "personal",
    zone_name: "PrimarySync",
    display_name: "旭刚",
    target_dir: "旭刚",
    enabled: true,
    status: "ok",
    interval_min: 30,
    nightly_at: "03:00",
    last_run_at: "2026-10-21T09:44:00+00:00",
    last_success_at: "2026-10-21T09:44:00+00:00",
    first_full_at: "2026-10-15T03:30:00+00:00",
    stats: { files: 901, last_added: 3, last_trashed: 0, last_success: "2026-10-21T09:44:00+00:00" },
  },
  {
    id: ids.libHuanyijing,
    account_id: ids.huanyijing,
    kind: "personal",
    zone_name: "PrimarySync",
    display_name: "幻伊镜",
    target_dir: "幻伊镜",
    enabled: true,
    status: "ok",
    interval_min: 30,
    nightly_at: "03:00",
    last_run_at: "2026-10-21T12:02:00+00:00",
    last_success_at: null,
    first_full_at: null,
    stats: {},
  },
];

const runs = (): Run[] => [
  {
    id: ids.runs[3],
    account_id: ids.huanyijing,
    library_id: null,
    mode: "auth",
    trigger: "onboarding",
    status: "running",
    started_at: "2026-10-21T12:02:00+00:00",
    ended_at: null,
    exit_code: null,
    stats: {},
    error_kind: null,
    error_message: null,
    plan_file: null,
  },
  {
    id: ids.runs[2],
    account_id: ids.xugang,
    library_id: ids.libXugang,
    mode: "sync",
    trigger: "schedule",
    status: "failed",
    started_at: "2026-10-21T10:17:00+00:00",
    ended_at: "2026-10-21T10:19:00+00:00",
    exit_code: -9,
    stats: {},
    error_kind: "killed",
    error_message: "worker killed",
    plan_file: null,
  },
  {
    id: ids.runs[1],
    account_id: ids.xugang,
    library_id: ids.libXugang,
    mode: "reconcile",
    trigger: "chain",
    status: "ok",
    started_at: "2026-10-21T09:44:00+00:00",
    ended_at: "2026-10-21T09:45:00+00:00",
    exit_code: 0,
    stats: { trashed: 0 },
    error_kind: null,
    error_message: null,
    plan_file: null,
  },
  {
    id: ids.runs[0],
    account_id: ids.xugang,
    library_id: ids.libXugang,
    mode: "sync",
    trigger: "manual",
    status: "ok",
    started_at: "2026-10-21T09:14:00+00:00",
    ended_at: "2026-10-21T09:44:00+00:00",
    exit_code: 0,
    stats: { downloaded: 3, trashed: 0 },
    error_kind: null,
    error_message: null,
    plan_file: null,
  },
];

const plans = (): Plan[] => [
  {
    id: ids.plan,
    library_id: ids.libHuanyijing,
    run_id: ids.runs[3],
    created_at: "2026-10-21T12:30:00+00:00",
    extraneous_count: 612,
    plan_file: "/data/plans/c1fa11ed.txt",
    status: "pending",
  },
];

const trash = (): TrashEntry[] => [
  {
    library_id: ids.libXugang,
    display_name: "旭刚",
    target_dir: "旭刚",
    days: [
      { day: "2026-10-21", files: 3, bytes: 12_582_912 },
      { day: "2026-10-19", files: 14, bytes: 2_147_483_648 },
    ],
  },
  {
    library_id: ids.libHuanyijing,
    display_name: "幻伊镜",
    target_dir: "幻伊镜",
    days: [{ day: "2026-10-20", files: 5, bytes: 52_428_800 }],
  },
];

const trashFiles = (): TrashFile[] => [
  { rel: "2026/10/IMG_0001.HEIC", size: 4_194_304 },
  { rel: "2026/10/IMG_0002.HEIC", size: 4_194_304 },
  { rel: "2026/10/IMG_0003.MOV", size: 4_194_304 },
];

const settingsValues = (): SettingsValues => ({
  max_workers: 2,
  min_free_gb: 5,
  threads_per_worker: 2,
  trash_retention_days: 30,
  log_retention_runs: 20,
  file_match_policy: "name-id7",
  live_photo_mov_filename_policy: "original",
  keep_unicode_in_filenames: true,
  folder_structure: "{:%Y/%m}",
  paused: false,
  apprise_urls: "",
  public_url: "",
  "notify.needs_code.enabled": true,
  "notify.password_rejected.enabled": true,
  "notify.repeated_failure.enabled": true,
  "notify.plan_pending.enabled": true,
  "notify.space_low.enabled": true,
  "notify.first_full.enabled": true,
});

const status = (): Status => ({
  paused: false,
  space_low: false,
  workers_running: 1,
  version: "0.0.1",
  started_at: "2026-10-21T08:00:00+00:00",
});

const hostUsers = (): HostUser[] => [
  { name: "Castamere", uid: 1000, gid: 10, group: "admin", home: "/home/Castamere" },
  { name: "Today_red", uid: 1001, gid: 10, group: "admin", home: "/home/Today_red" },
];

function freshDb() {
  return {
    accounts: accounts(),
    libraries: libraries(),
    runs: runs(),
    plans: plans(),
    trash: trash(),
    settings: { values: settingsValues(), admin_password_set: false, public_url_fallback: null } as SettingsResponse,
    status: status(),
    session: { auth_required: false, authenticated: false },
    adminPassword: ADMIN_PASSWORD,
    /** `GET /api/host-users`: false = single-user mode. */
    privileged: true,
    hostUsers: hostUsers(),
  };
}

/** Mutable fixture state; reset after every test by `resetMsw()`. */
export let db = freshDb();

/** Require the admin password (the fixture server then answers 401 until `POST /api/session`). */
export function requireAuth(): void {
  db.session = { auth_required: true, authenticated: false };
  db.settings.admin_password_set = true;
}

export interface Call {
  method: string;
  path: string;
  search: string;
  headers: Record<string, string>;
  credentials: RequestCredentials;
  body: unknown;
}

let recorded: Call[] = [];

/** Requests the client sent, in order. Bodies are parsed JSON (raw text if not JSON). */
export function calls(): Call[] {
  return recorded;
}

const err = (status: number, error: string, detail: string) =>
  HttpResponse.json({ error, detail }, { status });

const find = <T extends { id: string }>(items: T[], id: string | readonly string[] | undefined) =>
  items.find((item) => typeof id === "string" && (item.id === id || item.id.startsWith(id)));

const guarded =
  (resolver: HttpResponseResolver): HttpResponseResolver =>
  (info) => {
    if (db.session.auth_required && !db.session.authenticated) {
      return err(401, "unauthorized", "login required");
    }
    return resolver(info);
  };

async function record(request: Request): Promise<void> {
  const url = new URL(request.url);
  const call: Call = {
    method: request.method,
    path: url.pathname,
    search: url.search,
    headers: Object.fromEntries(request.headers.entries()),
    credentials: request.credentials,
    body: undefined,
  };
  recorded.push(call);
  if (request.method === "GET" || request.method === "HEAD") return;
  const text = await request.clone().text();
  try {
    call.body = text === "" ? undefined : JSON.parse(text);
  } catch {
    call.body = text;
  }
}

/** Records the request (body included) before resolving, so `calls()` is complete once a response arrives. */
const recording =
  (resolver: HttpResponseResolver): HttpResponseResolver =>
  async (info) => {
    await record(info.request);
    return resolver(info);
  };

type Method = "get" | "post" | "put" | "patch" | "delete";

const route = (method: Method, path: string, resolver: HttpResponseResolver) =>
  http[method](path, recording(resolver));

const handlers = [
  route("get", "/api/session", () => HttpResponse.json(db.session)),
  route("post", "/api/session", async ({ request }) => {
    const body = (await request.json()) as { password?: string };
    if (db.session.auth_required && body.password !== db.adminPassword) {
      return err(401, "unauthorized", "wrong password");
    }
    db.session = { ...db.session, authenticated: true };
    return new HttpResponse(null, {
      status: 204,
      headers: { "Set-Cookie": "i2n_session=signed; Path=/; HttpOnly; SameSite=Lax" },
    });
  }),
  route("delete", "/api/session", () => {
    db.session = { ...db.session, authenticated: false };
    return new HttpResponse(null, { status: 204 });
  }),

  route("get", "/api/status", guarded(() => HttpResponse.json(db.status))),
  route("post", 
    "/api/pause",
    guarded(() => {
      db.status = { ...db.status, paused: true };
      db.settings.values.paused = true;
      return HttpResponse.json({ paused: true });
    }),
  ),
  route("post", 
    "/api/resume",
    guarded(() => {
      db.status = { ...db.status, paused: false };
      db.settings.values.paused = false;
      return HttpResponse.json({ paused: false });
    }),
  ),

  route("get", "/api/accounts", guarded(() => HttpResponse.json(db.accounts))),
  route("post", 
    "/api/accounts",
    guarded(() =>
      HttpResponse.json({ id: "a3c0ffee000000000000000000000003", run_id: "d5000000000000000000000000000005" }, { status: 201 }),
    ),
  ),
  route("get", 
    "/api/accounts/:id",
    guarded(({ params }) => {
      const account = find(db.accounts, params.id);
      return account ? HttpResponse.json(account) : err(404, "not found", "unknown account");
    }),
  ),
  route("patch", 
    "/api/accounts/:id",
    guarded(async ({ params, request }) => {
      const account = find(db.accounts, params.id);
      if (!account) return err(404, "not found", "unknown account");
      const body = (await request.json()) as {
        display_name?: string;
        password?: string;
        owner_uid?: number;
        owner_gid?: number;
      };
      if ((body.owner_uid === undefined) !== (body.owner_gid === undefined)) {
        return err(400, "invalid", "owner_uid and owner_gid go together");
      }
      // the owner is fixed once the account is added; repeating it is a no-op (like the server)
      if (
        body.owner_uid !== undefined &&
        (body.owner_uid !== account.owner_uid || body.owner_gid !== account.owner_gid)
      ) {
        return err(409, "owner fixed", "the file owner is set when the account is added and cannot be changed");
      }
      if (body.display_name) account.display_name = body.display_name;
      return HttpResponse.json(
        body.password ? { ...account, run_id: "d6000000000000000000000000000006" } : account,
      );
    }),
  ),
  route("delete", 
    "/api/accounts/:id",
    guarded(({ params }) => {
      const account = find(db.accounts, params.id);
      if (!account) return err(404, "not found", "unknown account");
      db.accounts = db.accounts.filter((a) => a.id !== account.id);
      db.libraries = db.libraries.filter((l) => l.account_id !== account.id);
      return new HttpResponse(null, { status: 204 });
    }),
  ),
  route("post", 
    "/api/accounts/:id/auth",
    guarded(() => HttpResponse.json({ run_id: "d7000000000000000000000000000007" }, { status: 202 })),
  ),
  route("post", 
    "/api/accounts/:id/code",
    guarded(({ params }) => {
      const account = find(db.accounts, params.id);
      if (!account) return err(404, "not found", "unknown account");
      if (account.status !== "awaiting_code") {
        return err(409, "busy", "no login is waiting for a code");
      }
      return new HttpResponse(null, { status: 204 });
    }),
  ),
  route("post", 
    "/api/accounts/:id/refresh",
    guarded(() => HttpResponse.json({ personal: ["PrimarySync"], shared: [] })),
  ),

  route(
    "get",
    "/api/host-users",
    guarded(({ request }) => {
      const path = new URL(request.url).searchParams.get("path");
      if (path !== null && (path.startsWith("/") || path.split("/").includes(".."))) {
        return err(400, "invalid", "path must stay inside the photos root");
      }
      // the nearest existing ancestor of `<name>/…` is that user's home
      const first = path?.split("/")[0];
      const user = db.hostUsers.find((u) => u.name === first);
      return HttpResponse.json({
        privileged: db.privileged,
        users: db.hostUsers,
        suggested: user ? { uid: user.uid, gid: user.gid } : null,
      });
    }),
  ),

  route("get", "/api/libraries", guarded(() => HttpResponse.json(db.libraries))),
  route("get", 
    "/api/libraries/:id",
    guarded(({ params }) => {
      const library = find(db.libraries, params.id);
      return library ? HttpResponse.json(library) : err(404, "not found", "unknown library");
    }),
  ),
  route("patch", 
    "/api/libraries/:id",
    guarded(async ({ params, request }) => {
      const library = find(db.libraries, params.id);
      if (!library) return err(404, "not found", "unknown library");
      const body = (await request.json()) as Partial<Library>;
      if (body.nightly_at !== undefined) {
        const m = /^(\d{1,2}):(\d{2})$/.exec(body.nightly_at);
        if (!m || Number(m[1]) > 23 || Number(m[2]) > 59) return err(400, "invalid", "nightly_at must be HH:MM");
        body.nightly_at = `${m[1]!.padStart(2, "0")}:${m[2]}`;
      }
      Object.assign(library, body);
      return HttpResponse.json(library);
    }),
  ),
  ...(["sync", "reconcile"] as const).map((mode) =>
    route(
      "post",
      `/api/libraries/:id/${mode}`,
      guarded(({ params }) => {
        if (find(db.libraries, params.id)?.status === "dir_missing") {
          return err(409, "busy", "library folder is missing");
        }
        return HttpResponse.json({ queued: true }, { status: 202 });
      }),
    ),
  ),
  route("post", 
    "/api/libraries/:id/pause",
    guarded(({ params }) => {
      const library = find(db.libraries, params.id);
      if (!library) return err(404, "not found", "unknown library");
      library.status = "paused";
      return HttpResponse.json(library);
    }),
  ),
  route("post", 
    "/api/libraries/:id/resume",
    guarded(({ params }) => {
      const library = find(db.libraries, params.id);
      if (!library) return err(404, "not found", "unknown library");
      library.status = "ok";
      return HttpResponse.json(library);
    }),
  ),

  route("get", 
    "/api/runs",
    guarded(({ request }) => {
      const q = new URL(request.url).searchParams;
      const library = q.get("library_id");
      const account = q.get("account_id");
      const limit = Number(q.get("limit") ?? 50);
      const out = db.runs
        .filter((r) => !library || r.library_id === library)
        .filter((r) => !account || r.account_id === account)
        .slice(0, limit);
      return HttpResponse.json(out);
    }),
  ),
  route("get", 
    "/api/runs/:id",
    guarded(({ params }) => {
      const run = find(db.runs, params.id);
      return run ? HttpResponse.json(run) : err(404, "not found", "unknown run");
    }),
  ),
  route("get", 
    "/api/runs/:id/log",
    guarded(({ params }) => {
      const run = find(db.runs, params.id);
      if (!run) return err(404, "not found", "no log");
      return HttpResponse.text(`${run.started_at} start ${run.mode}\n${run.started_at} done\n`);
    }),
  ),

  route("get", 
    "/api/plans",
    guarded(({ request }) => {
      const wanted = new URL(request.url).searchParams.get("status") ?? "pending";
      return HttpResponse.json(wanted === "all" ? db.plans : db.plans.filter((p) => p.status === wanted));
    }),
  ),
  route("get", 
    "/api/plans/:id",
    guarded(({ params }) => {
      const plan = find(db.plans, params.id);
      if (!plan) return err(404, "not found", "unknown plan");
      const paths = Array.from({ length: Math.min(1000, plan.extraneous_count) }, (_, i) => `2024/0${(i % 9) + 1}/IMG_${String(i).padStart(4, "0")}.HEIC`);
      const detail: PlanDetail = { ...plan, paths, total: plan.extraneous_count };
      return HttpResponse.json(detail);
    }),
  ),
  route("post", 
    "/api/plans/:id/approve",
    guarded(({ params }) => {
      const plan = find(db.plans, params.id);
      if (!plan) return err(404, "not found", "unknown plan");
      if (plan.status !== "pending") return err(400, "invalid", "plan is not pending");
      plan.status = "approved";
      return HttpResponse.json(plan, { status: 202 });
    }),
  ),
  route("post", 
    "/api/plans/:id/reject",
    guarded(({ params }) => {
      const plan = find(db.plans, params.id);
      if (!plan) return err(404, "not found", "unknown plan");
      if (plan.status !== "pending") return err(400, "invalid", "plan is not pending");
      plan.status = "rejected";
      return HttpResponse.json(plan);
    }),
  ),

  route("get", "/api/trash", guarded(() => HttpResponse.json(db.trash))),
  route("get", "/api/trash/:day", guarded(() => HttpResponse.json(trashFiles()))),
  route("delete", 
    "/api/trash/:day",
    guarded(({ params, request }) => {
      const library = new URL(request.url).searchParams.get("library");
      const entry = db.trash.find((e) => e.library_id === library);
      const day = entry?.days.find((d) => d.day === params.day);
      if (!entry || !day) return err(404, "not found", "no such trash day");
      entry.days = entry.days.filter((d) => d !== day);
      return HttpResponse.json({ deleted: day.files });
    }),
  ),
  route("post", 
    "/api/trash/:day/keep",
    guarded(async ({ request }) => {
      const body = (await request.json()) as { paths: string[] };
      return HttpResponse.json({ kept: body.paths });
    }),
  ),

  route("get", "/api/settings", guarded(() => HttpResponse.json(db.settings))),
  route("put", 
    "/api/settings",
    guarded(async ({ request }) => {
      const body = (await request.json()) as Record<string, string>;
      const values = db.settings.values as unknown as Record<string, unknown>;
      for (const [key, value] of Object.entries(body)) {
        if (key === "admin_password") {
          db.settings.admin_password_set = value !== "";
          // As the real server: setting requires the cookie from then on, but issues none itself.
          db.session = { auth_required: value !== "", authenticated: false };
          if (value !== "") db.adminPassword = value;
          continue;
        }
        if (!(key in values)) return err(400, "invalid", `unknown setting ${key}`);
        const current = values[key];
        values[key] =
          typeof current === "boolean" ? value === "1" : typeof current === "number" ? Number(value) : value;
      }
      return HttpResponse.json(db.settings);
    }),
  ),
  route("post", 
    "/api/notify/test",
    guarded(() =>
      db.settings.values.apprise_urls
        ? HttpResponse.json({ sent: true })
        : err(400, "invalid", "apprise_urls is empty"),
    ),
  ),
];

export const server = setupServer(...handlers);

/**
 * Override one route for the current test: a JSON value (answered 200, or `status`),
 * or a full MSW resolver.
 */
export function given(
  method: Method | Uppercase<Method>,
  path: string,
  response: unknown,
  status = 200,
): void {
  const m = method.toLowerCase() as Method;
  const resolver: HttpResponseResolver =
    typeof response === "function"
      ? (response as HttpResponseResolver)
      : () =>
          status === 204
            ? new HttpResponse(null, { status })
            : HttpResponse.json(response as Parameters<typeof HttpResponse.json>[0], { status });
  server.use(route(m, path, resolver));
}

/** Reset handlers, fixture data and recorded calls (setup.ts runs it after each test). */
export function resetMsw(): void {
  server.resetHandlers();
  db = freshDb();
  recorded = [];
}
