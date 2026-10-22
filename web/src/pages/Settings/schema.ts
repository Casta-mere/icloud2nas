import type { SettingsKey, SettingsValues } from "../../api/types";
import type { Key as I18nKey } from "../../i18n";

export type FieldKey = Exclude<SettingsKey, "paused"> | "admin_password";

export type Field = {
  key: FieldKey;
  group: "notify" | "sync" | "retention";
  kind: "text" | "textarea" | "int" | "float" | "select" | "toggle" | "password";
  labelKey: I18nKey;
  min?: number;
  max?: number;
  options?: readonly string[];
  hintKey?: I18nKey;
  warnKey?: I18nKey;
  mono?: boolean;
};

const event = (name: string, labelKey: I18nKey): Field => ({
  key: `notify.${name}.enabled` as FieldKey,
  group: "notify",
  kind: "toggle",
  labelKey,
});

export const FIELDS: readonly Field[] = [
  { key: "apprise_urls", group: "notify", kind: "textarea", labelKey: "field-apprise-urls", mono: true },
  { key: "public_url", group: "notify", kind: "text", labelKey: "field-public-url" },
  event("needs_code", "event-needs-code"),
  event("password_rejected", "event-password-rejected"),
  event("repeated_failure", "event-repeated-failure"),
  event("plan_pending", "event-plan-pending"),
  event("space_low", "event-space-low"),
  event("first_full", "event-first-full"),
  { key: "max_workers", group: "sync", kind: "int", labelKey: "field-max-workers", min: 1, max: 8, hintKey: "hint-max-workers" },
  { key: "threads_per_worker", group: "sync", kind: "int", labelKey: "field-threads", min: 1, max: 8 },
  { key: "min_free_gb", group: "sync", kind: "float", labelKey: "field-min-free", min: 0, hintKey: "hint-min-free" },
  {
    key: "file_match_policy",
    group: "sync",
    kind: "select",
    labelKey: "field-file-match",
    options: ["name-id7", "name-size-dedup-with-suffix"],
    warnKey: "policy-change-warning",
  },
  { key: "folder_structure", group: "sync", kind: "text", labelKey: "field-folder-structure", mono: true, warnKey: "policy-change-warning" },
  { key: "live_photo_mov_filename_policy", group: "sync", kind: "select", labelKey: "field-live-mov", options: ["original", "suffix"] },
  { key: "keep_unicode_in_filenames", group: "sync", kind: "toggle", labelKey: "field-keep-unicode" },
  { key: "trash_retention_days", group: "retention", kind: "int", labelKey: "field-trash-days", min: 0, hintKey: "hint-trash-days" },
  { key: "log_retention_runs", group: "retention", kind: "int", labelKey: "field-log-runs", min: 1, hintKey: "hint-log-runs" },
  { key: "admin_password", group: "retention", kind: "password", labelKey: "field-admin-password" },
];

export function toWire(value: string | number | boolean, field: Field): string {
  if (field.kind === "toggle") return value === true || value === "1" ? "1" : "0";
  return String(value);
}

export function validate(field: Field, value: string): I18nKey | null {
  if (field.kind === "int" || field.kind === "float") {
    const n = Number(value);
    if (value.trim() === "" || !Number.isFinite(n)) return "err-number";
    if (field.kind === "int" && !Number.isInteger(n)) return "err-number";
    if ((field.min !== undefined && n < field.min) || (field.max !== undefined && n > field.max)) return "err-range";
  }
  if (field.kind === "select" && field.options && !field.options.includes(value)) return "err-option";
  return null;
}

/** The wire-string form of the loaded settings (booleans "1"/"0", numbers String). */
export function fromValues(values: SettingsValues): Record<Exclude<FieldKey, "admin_password">, string> {
  const out: Record<string, string> = {};
  for (const f of FIELDS) {
    if (f.key === "admin_password") continue;
    out[f.key] = toWire(values[f.key], f);
  }
  return out;
}
