import { useCallback, useState, type FormEvent } from "react";
import { ApiError } from "../../api/client";
import { useCreateAccount, useLibraries } from "../../api/queries";
import type { Region } from "../../api/types";
import { useT, type Key } from "../../i18n";
import { guardImeSubmit } from "../../lib/ime";
import { defaultDir, nests, normalizeRel } from "../../lib/paths";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Chip } from "../../ui/Chip";
import { FormRow } from "../../ui/FormRow";
import { CodeStep } from "./CodeStep";
import { DiscoverStep } from "./DiscoverStep";
import { AUTO_OWNER, OwnerSelect, type OwnerChoice } from "./OwnerSelect";

type Step = 1 | 2 | 3 | 4;
const STEPS: [Step, Key][] = [
  [1, "step-who-where"],
  [2, "step-apple-id"],
  [3, "step-code"],
  [4, "step-libraries"],
];

const INPUT =
  "w-full min-w-0 rounded-[10px] border-2 border-ink bg-white px-2.5 py-1.5 text-[13px]";

export function AddAccountWizard({ onDone }: { onDone: () => void }) {
  const t = useT();
  const libraries = useLibraries();
  const create = useCreateAccount();
  const [step, setStep] = useState<Step>(1);
  const [name, setName] = useState("");
  const [dirInput, setDirInput] = useState<string | null>(null);
  const [region, setRegion] = useState<Region>("cn");
  const [owner, setOwner] = useState<OwnerChoice>(AUTO_OWNER);
  const [appleId, setAppleId] = useState("");
  const [password, setPassword] = useState("");
  const [created, setCreated] = useState<{ id: string; run_id: string } | null>(null);
  const [serverDirError, setServerDirError] = useState<string | null>(null);
  const [appleError, setAppleError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const dir = dirInput ?? defaultDir(name);
  const rel = normalizeRel(dir);
  const conflict = rel === null ? undefined : (libraries.data ?? []).find((l) => nests(rel, l.target_dir));
  const dirError =
    serverDirError ??
    (dir.trim() === "" && dirInput === null
      ? null
      : rel === null
        ? t("dir-invalid")
        : conflict
          ? t("dir-nests", { name: conflict.display_name })
          : null);
  const step1Valid = name.trim() !== "" && rel !== null && !conflict && !serverDirError && owner.valid;
  const verified = useCallback(() => setStep((s) => (s === 3 ? 4 : s)), []);

  const submit2 = (e: FormEvent) => {
    e.preventDefault();
    if (rel === null || create.isPending) return;
    setAppleError(null);
    setFormError(null);
    create.mutate(
      {
        display_name: name.trim(),
        apple_id: appleId.trim(),
        region,
        password,
        target_dir: rel,
        // hidden in single-user mode, so then never set
        ...(owner.owner ? { owner_uid: owner.owner.uid, owner_gid: owner.owner.gid } : {}),
      },
      {
        onSuccess: (r) => {
          setCreated(r);
          setStep(3);
        },
        onError: (err) => {
          const detail = err instanceof ApiError ? err.detail : String(err);
          if (err instanceof ApiError && err.status === 409) {
            setAppleError(t("apple-id-exists-detail", { detail }));
          } else if (err instanceof ApiError && err.status === 400 && /target_dir|directory/i.test(detail)) {
            setServerDirError(detail);
            setStep(1);
          } else {
            setFormError(detail);
          }
        },
      },
    );
  };

  return (
    <Card tone="soft">
      <h2 className="m-0 mb-2 text-lg font-extrabold">{t("add-account")}</h2>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {STEPS.map(([n, key]) => (
          <span key={n} aria-current={n === step ? "step" : undefined}>
            <Chip tone={n === step ? "ok" : "neutral"}>
              {n} {t(key)}
            </Chip>
          </span>
        ))}
      </div>

      {step === 1 ? (
        <form
          className="grid gap-3"
          onKeyDownCapture={guardImeSubmit}
          onSubmit={(e) => {
            e.preventDefault();
            if (step1Valid) setStep(2);
          }}
        >
          <FormRow label={t("display-name")}>
            <input className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
          </FormRow>
          <FormRow
            label={t("photo-dir")}
            error={dirError}
            hint={
              <>
                <div className="font-mono">{rel === null ? "" : `/photos/${rel}`}</div>
                <div>{t("dir-hint")}</div>
              </>
            }
          >
            <input
              className={`${INPUT} font-mono`}
              value={dir}
              onChange={(e) => {
                setDirInput(e.target.value);
                setServerDirError(null);
              }}
            />
          </FormRow>
          <OwnerSelect folderRel={rel} value={owner} onChange={setOwner} />
          <FormRow label={t("region")} alignTop>
            <div role="radiogroup" aria-label={t("region")} className="grid gap-1.5 text-[13px]">
              {(["cn", "com"] as const).map((r) => (
                <label key={r} className="flex items-center gap-2">
                  <input type="radio" name="region" checked={region === r} onChange={() => setRegion(r)} />
                  {t(r === "cn" ? "region-cn" : "region-com")}
                </label>
              ))}
            </div>
          </FormRow>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onDone}>
              {t("cancel")}
            </Button>
            <Button variant="primary" type="submit" disabled={!step1Valid}>
              {t("next-step")}
            </Button>
          </div>
        </form>
      ) : null}

      {step === 2 ? (
        <form className="grid gap-3" onKeyDownCapture={guardImeSubmit} onSubmit={submit2}>
          <FormRow label={t("apple-id")} error={appleError}>
            <input
              className={`${INPUT} font-mono`}
              type="email"
              autoComplete="off"
              value={appleId}
              onChange={(e) => setAppleId(e.target.value)}
            />
          </FormRow>
          <FormRow label={t("password")}>
            <input
              className={INPUT}
              type="password"
              autoComplete="off"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </FormRow>
          {formError ? (
            <div role="alert" className="text-[12px] text-[#991b1b]">
              {formError}
            </div>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={() => setStep(1)}>
              {t("prev-step")}
            </Button>
            <Button
              variant="primary"
              type="submit"
              disabled={create.isPending || appleId.trim() === "" || password === ""}
            >
              {t("next-step")}
            </Button>
          </div>
        </form>
      ) : null}

      {step === 3 && created ? (
        <CodeStep accountId={created.id} runId={created.run_id} onVerified={verified} onCancel={onDone} />
      ) : null}
      {step === 4 && created ? <DiscoverStep accountId={created.id} onDone={onDone} /> : null}
    </Card>
  );
}
