// Shapes from docs/api.md. Timestamps are ISO-8601 strings (aware UTC).

export type AccountStatus = "new" | "awaiting_code" | "ok" | "needs_reauth" | "password_rejected";
export type Region = "cn" | "com";
export type LibraryKind = "personal" | "shared";
export type LibraryStatus = "ok" | "paused" | "space_paused";
export type RunMode = "auth" | "list" | "sync" | "reconcile";
export type RunTrigger = "onboarding" | "reauth" | "schedule" | "nightly" | "chain" | "manual";
export type RunStatus = "queued" | "running" | "ok" | "failed" | "needs_mfa" | "fused";
export type PlanStatus = "pending" | "approved" | "executed" | "rejected" | "superseded";

export interface Account {
  id: string;
  display_name: string;
  apple_id: string;
  region: Region;
  status: AccountStatus;
  failures: number;
  backoff_until: string | null;
  last_auth_ok_at: string | null;
  last_error: string | null;
  created_at: string;
  primary_dir: string | null;
  library_ids: string[];
}

export interface LibraryStats {
  files?: number | null;
  last_added?: number;
  last_trashed?: number;
  last_success?: string;
}

export interface Library {
  id: string;
  account_id: string;
  kind: LibraryKind;
  zone_name: string;
  display_name: string;
  target_dir: string;
  enabled: boolean;
  status: LibraryStatus;
  interval_min: number;
  nightly_at: string;
  last_run_at: string | null;
  last_success_at: string | null;
  first_full_at: string | null;
  stats: LibraryStats;
}

export interface Run {
  id: string;
  account_id: string;
  library_id: string | null;
  mode: RunMode;
  trigger: RunTrigger;
  status: RunStatus;
  started_at: string;
  ended_at: string | null;
  exit_code: number | null;
  stats: Record<string, unknown>;
  error_kind: string | null;
  error_message: string | null;
  plan_file: string | null;
}

export interface RunFilters {
  library_id?: string;
  account_id?: string;
  limit?: number;
}

export interface Plan {
  id: string;
  library_id: string;
  run_id: string;
  created_at: string;
  extraneous_count: number;
  plan_file: string;
  status: PlanStatus;
}

export interface PlanDetail extends Plan {
  paths: string[];
  total: number;
}

export interface TrashDay {
  day: string;
  files: number;
  bytes: number;
}

export interface TrashEntry {
  rel: string;
  library_id: string | null;
  display_name: string | null;
  days: TrashDay[];
}

export interface TrashFile {
  rel: string;
  size: number;
}

export interface SettingsValues {
  max_workers: number;
  min_free_gb: number;
  threads_per_worker: number;
  trash_retention_days: number;
  log_retention_runs: number;
  file_match_policy: string;
  live_photo_mov_filename_policy: string;
  keep_unicode_in_filenames: boolean;
  folder_structure: string;
  paused: boolean;
  apprise_urls: string;
  public_url: string;
  "notify.needs_code.enabled": boolean;
  "notify.password_rejected.enabled": boolean;
  "notify.repeated_failure.enabled": boolean;
  "notify.plan_pending.enabled": boolean;
  "notify.space_low.enabled": boolean;
  "notify.first_full.enabled": boolean;
}

export type SettingsKey = keyof SettingsValues;

export interface SettingsResponse {
  values: SettingsValues;
  admin_password_set: boolean;
  public_url_fallback: string | null;
}

/** PUT /api/settings body: values are strings (booleans "1"/"0"); `admin_password: ""` clears it. */
export type SettingsUpdate = Partial<Record<SettingsKey, string>> & { admin_password?: string };

export interface Status {
  paused: boolean;
  space_low: boolean;
  workers_running: number;
  version: string;
  started_at: string;
}

export interface Session {
  auth_required: boolean;
  authenticated: boolean;
}

export interface NewAccount {
  display_name: string;
  apple_id: string;
  region: Region;
  password: string;
  target_dir?: string;
}

export interface LibraryPatch {
  display_name?: string;
  target_dir?: string;
  enabled?: boolean;
  interval_min?: number;
  nightly_at?: string;
}

export interface WorkerEvent {
  ev: string;
  [field: string]: unknown;
}

export interface EventPayloads {
  hello: { server_time: string };
  "run.started": {
    run_id: string;
    account_id: string;
    library_id: string | null;
    mode: RunMode;
    trigger: RunTrigger;
  };
  "run.event": { run_id: string; event: WorkerEvent };
  "run.finished": {
    run_id: string;
    status: RunStatus;
    library_id: string | null;
    account_id: string;
    mode: RunMode;
    trigger: RunTrigger;
    exit_code: number | null;
    first_full: boolean;
    account_failures: number;
  };
  "account.status": { account_id: string; status: AccountStatus };
  "account.updated": { account_id: string; deleted?: boolean };
  "library.updated": { library_id: string };
  "plan.pending": { plan_id: string; extraneous: number; library_id: string };
  "plan.resolved": { plan_id: string; library_id: string; status: PlanStatus };
  "space.low": { min_free_gb: number };
  "space.ok": { min_free_gb: number };
  paused: { paused: boolean };
  "settings.updated": { keys: string[] };
}

export type EventTopic = Exclude<keyof EventPayloads, "hello">;
