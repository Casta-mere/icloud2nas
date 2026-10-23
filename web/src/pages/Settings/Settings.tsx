import { useState, type ReactNode } from "react";
import { ApiError } from "../../api/client";
import { useNotifyTest, usePutSettings, useSettings } from "../../api/queries";
import type { SettingsUpdate } from "../../api/types";
import { useT } from "../../i18n";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { FormRow } from "../../ui/FormRow";
import { QueryState } from "../../ui/QueryState";
import { Toggle } from "../../ui/Toggle";
import { AdminPassword } from "./AdminPassword";
import { FieldRow } from "./FieldRow";
import { FIELDS, fromValues, validate, type Field, type FieldKey } from "./schema";

type DataKey = Exclude<FieldKey, "admin_password">;
const DATA_FIELDS = FIELDS.filter((f) => f.kind !== "password");
const inGroup = (g: Field["group"]) => DATA_FIELDS.filter((f) => f.group === g);

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card tone="soft">
      <h4 className="mb-3 text-[15px] font-extrabold">{title}</h4>
      <div className="grid gap-3">{children}</div>
    </Card>
  );
}

export function Settings() {
  const t = useT();
  const settings = useSettings();
  const put = usePutSettings();
  const notify = useNotifyTest();
  const [draft, setDraft] = useState<Partial<Record<DataKey, string>>>({});
  const [serverError, setServerError] = useState<{ key: string; detail: string } | null>(null);

  if (!settings.data) return <QueryState queries={[settings]} />;
  const { values, admin_password_set, public_url_fallback } = settings.data;
  const base = fromValues(values);
  const valueOf = (k: DataKey) => draft[k] ?? base[k];
  const dirty = (Object.keys(draft) as DataKey[]).filter((k) => draft[k] !== base[k]);
  const clientError = (f: Field) =>
    dirty.includes(f.key as DataKey) ? validate(f, valueOf(f.key as DataKey)) : null;
  const invalid = DATA_FIELDS.some((f) => clientError(f) !== null);

  const edit = (k: DataKey, v: string) => {
    setServerError(null);
    setDraft((d) => {
      const next = { ...d };
      if (v === base[k]) delete next[k];
      else next[k] = v;
      return next;
    });
  };
  const save = () => {
    const body: SettingsUpdate = {};
    for (const k of dirty) body[k] = valueOf(k);
    setServerError(null);
    put.mutate(body, {
      onSuccess: () => setDraft({}),
      onError: (e) => {
        const detail = e instanceof ApiError ? e.detail : e.message;
        setServerError({ key: detail.split(":")[0] ?? "", detail });
      },
    });
  };

  const row = (f: Field) => {
    const k = f.key as DataKey;
    const ce = clientError(f);
    const hint =
      k === "public_url"
        ? valueOf(k) === ""
          ? t("public-url-fallback", { url: public_url_fallback ?? "—" })
          : t("public-url-hint")
        : undefined;
    return (
      <FieldRow
        key={k}
        field={f}
        value={valueOf(k)}
        onChange={(v) => edit(k, v)}
        hint={hint}
        error={ce ? t(ce) : serverError?.key === k ? serverError.detail : null}
      />
    );
  };

  const notifyFields = inGroup("notify");
  const events = notifyFields.filter((f) => f.kind === "toggle");
  const notifyResult = notify.isPending
    ? ""
    : notify.error
      ? notify.error instanceof ApiError
        ? notify.error.detail
        : notify.error.message
      : notify.data
        ? t(notify.data.sent ? "notify-sent" : "notify-failed")
        : "";
  const unknownError =
    serverError && !DATA_FIELDS.some((f) => f.key === serverError.key) ? serverError.detail : null;

  return (
    <div>
      <div className="grid grid-cols-1 items-start gap-4 min-[960px]:grid-cols-[1.1fr_.9fr]">
        <Group title={t("settings-notify")}>
          {row(notifyFields[0] as Field)}
          <FormRow label="">
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="ghost" disabled={notify.isPending} onClick={() => notify.mutate()}>
                {t("notify-test")}
              </Button>
              {notifyResult && (
                <span role="status" className="min-w-0 text-xs text-[#6b7280]">
                  {notifyResult}
                </span>
              )}
            </div>
          </FormRow>
          {row(notifyFields[1] as Field)}
          <FormRow label={t("field-events")} alignTop htmlFor="">
            <div className="grid gap-1.5">
              {events.map((f) => (
                <Toggle
                  key={f.key}
                  checked={valueOf(f.key as DataKey) === "1"}
                  onChange={(c) => edit(f.key as DataKey, c ? "1" : "0")}
                  label={t(f.labelKey)}
                />
              ))}
            </div>
          </FormRow>
        </Group>
        <div className="grid content-start gap-4">
          <Group title={t("settings-sync")}>{inGroup("sync").map(row)}</Group>
          <Group title={t("settings-retention")}>
            {inGroup("retention").map(row)}
            <AdminPassword isSet={admin_password_set} />
          </Group>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
        <span className="mr-auto text-xs text-[#6b7280]">{t("settings-note")}</span>
        {unknownError && (
          <span role="alert" className="text-xs text-[#9a3412]">
            {t("err-form")}
            {unknownError}
          </span>
        )}
        <Button variant="ghost" disabled={dirty.length === 0} onClick={() => setDraft({})}>
          {t("discard")}
        </Button>
        <Button variant="primary" disabled={dirty.length === 0 || invalid || put.isPending} onClick={save}>
          {t("save-n", { n: dirty.length })}
        </Button>
      </div>
    </div>
  );
}
