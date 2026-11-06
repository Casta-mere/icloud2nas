import { useEffect, useId, useState, type ReactNode } from "react";
import { useLocation } from "react-router";
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
import { SaveBar } from "./SaveBar";
import { FIELDS, fromValues, validate, type Field, type FieldKey } from "./schema";
import { VersionInfo } from "./VersionInfo";

type DataKey = Exclude<FieldKey, "admin_password">;
const DATA_FIELDS = FIELDS.filter((f) => f.kind !== "password");
const inGroup = (g: Field["group"]) => DATA_FIELDS.filter((f) => f.group === g);
const errorText = (e: Error) => (e instanceof ApiError ? e.detail : e.message);

function Group({ id, title, children }: { id?: string; title: string; children: ReactNode }) {
  const heading = useId();
  return (
    <section id={id} aria-labelledby={heading} className="min-w-0">
      <Card tone="soft">
        <h4 id={heading} className="mb-3 text-[15px] font-extrabold">
          {title}
        </h4>
        <div className="grid gap-3">{children}</div>
      </Card>
    </section>
  );
}

export function Settings() {
  const t = useT();
  const settings = useSettings();
  const put = usePutSettings();
  const notify = useNotifyTest();
  // saves just apprise_urls after a successful test of an edited value
  const saveUrls = usePutSettings();
  const [draft, setDraft] = useState<Partial<Record<DataKey, string>>>({});
  const [serverError, setServerError] = useState<{ key: string; detail: string } | null>(null);
  const { hash, key: visit } = useLocation();
  const loaded = settings.data !== undefined;

  // `/settings#about` lands on the About card once the page has laid out (again on every visit)
  useEffect(() => {
    if (loaded && hash === "#about") document.getElementById("about")?.scrollIntoView?.({ block: "start" });
  }, [loaded, hash, visit]);

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
        const detail = errorText(e);
        setServerError({ key: detail.split(":")[0] ?? "", detail });
      },
    });
  };
  // 发一条测试 sends what is in the box; an edited value that worked is saved on its own
  const testNotify = () => {
    const urls = valueOf("apprise_urls");
    const edited = dirty.includes("apprise_urls");
    saveUrls.reset();
    notify.mutate(
      { apprise_urls: urls },
      {
        onSuccess: ({ sent }) => {
          if (!sent || !edited) return;
          saveUrls.mutate(
            { apprise_urls: urls },
            {
              onSuccess: () =>
                setDraft((d) => {
                  if (d.apprise_urls !== urls) return d; // typed on while saving: keep the newer text
                  const next = { ...d };
                  delete next.apprise_urls;
                  return next;
                }),
            },
          );
        },
      },
    );
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
  const testing = notify.isPending || saveUrls.isPending;
  const notifyResult = testing
    ? ""
    : notify.error
      ? errorText(notify.error)
      : notify.data
        ? t(!notify.data.sent ? "notify-failed" : saveUrls.isSuccess ? "notify-sent-saved" : "notify-sent")
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
              <Button variant="ghost" disabled={testing} onClick={testNotify}>
                {t("notify-test")}
              </Button>
              {notifyResult && (
                <span role="status" className="min-w-0 text-xs text-[#6b7280]">
                  {notifyResult}
                </span>
              )}
              {saveUrls.error && (
                <span role="alert" className="min-w-0 text-xs text-[#9a3412]">
                  {t("err-form")}
                  {errorText(saveUrls.error)}
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
          <Group id="about" title={t("settings-about")}>
            <VersionInfo />
            {inGroup("about").map(row)}
          </Group>
        </div>
      </div>
      <SaveBar
        count={dirty.length}
        canSave={!invalid && !put.isPending && !saveUrls.isPending}
        error={unknownError}
        onDiscard={() => {
          setServerError(null);
          setDraft({});
        }}
        onSave={save}
      />
    </div>
  );
}
