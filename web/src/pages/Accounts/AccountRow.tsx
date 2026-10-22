import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../api/client";
import { usePatchAccount, useRefreshAccount } from "../../api/queries";
import type { Account, EventPayloads } from "../../api/types";
import { appEvents } from "../../events/useEventStream";
import { useT, type Key } from "../../i18n";
import { fmtDateTime } from "../../lib/format";
import { guardImeSubmit } from "../../lib/ime";
import { Button } from "../../ui/Button";
import { Chip, type ChipTone } from "../../ui/Chip";
import { CodeInput } from "../../ui/CodeInput";
import { useCodeFlow } from "../../events/useCodeFlow";

function statusChip(status: Account["status"]): { tone: ChipTone; key: Key } {
  switch (status) {
    case "awaiting_code":
    case "needs_reauth":
      return { tone: "warn", key: "status-awaiting-code" };
    case "password_rejected":
      return { tone: "fuse", key: "status-password-rejected" };
    case "ok":
      return { tone: "ok", key: "status-ok" };
    default:
      return { tone: "neutral", key: "status-new" };
  }
}

const INPUT = "w-full min-w-0 rounded-[10px] border-2 border-ink bg-white px-2.5 py-1.5 text-[13px]";
const message = (e: unknown) => (e instanceof ApiError ? e.detail : e instanceof Error ? e.message : null);

type Editing = "none" | "name" | "password";

export function AccountRow({ account, onDelete }: { account: Account; onDelete: (a: Account) => void }) {
  const t = useT();
  const patch = usePatchAccount();
  const refresh = useRefreshAccount();
  const [menu, setMenu] = useState(false);
  const [editing, setEditing] = useState<Editing>("none");
  const [name, setName] = useState(account.display_name);
  const [password, setPassword] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [codeOpen, setCodeOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const flow = useCodeFlow(account.id, { onNeedCode: () => setCodeOpen(true) });

  useEffect(() => {
    if (!menu) return;
    const onDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  useEffect(() => {
    const onFinished = (e: Event) => {
      const p = (e as CustomEvent<EventPayloads["run.finished"]>).detail;
      if (p.mode === "auth" && p.account_id === account.id) setCodeOpen(false);
    };
    appEvents.addEventListener("run.finished", onFinished);
    return () => appEvents.removeEventListener("run.finished", onFinished);
  }, [account.id]);

  const chip = statusChip(account.status);
  const pick = (what: Editing) => {
    setMenu(false);
    setEditing(what);
    setName(account.display_name);
    setPassword("");
    setNameError(null);
  };

  const saveName = (e: FormEvent) => {
    e.preventDefault();
    const next = name.trim();
    if (next === "") {
      setNameError(t("name-required"));
      return;
    }
    patch.mutate({ id: account.id, display_name: next }, { onSuccess: () => setEditing("none") });
  };

  const savePassword = (e: FormEvent) => {
    e.preventDefault();
    if (password === "") return;
    patch.mutate(
      { id: account.id, password },
      {
        onSuccess: (r) => {
          if (r.run_id) flow.track(r.run_id);
          setEditing("none");
          setPassword("");
          setCodeOpen(true);
        },
      },
    );
  };

  // the code panel opens only once `/auth` accepted the login (or `need_mfa` arrives)
  const reauth = () => flow.resend(() => setCodeOpen(true));
  const actionError = message(patch.error) ?? flow.actionError ?? message(refresh.error);

  return (
    <article aria-label={account.display_name} className="border-t-2 border-[rgba(17,17,17,.12)] py-3 first:border-t-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0 flex-1 basis-[220px]">
          <div className="flex min-w-0 items-center gap-2 text-[17px] font-extrabold">
            <span className="truncate">{account.display_name}</span>
            <Chip tone={chip.tone}>{t(chip.key)}</Chip>
          </div>
          <div className="truncate text-[12px] text-[#6b7280] tabular-nums">
            <span className="font-mono">{account.apple_id}</span> · {account.region}
            {account.last_auth_ok_at ? ` · ${t("last-login", { time: fmtDateTime(account.last_auth_ok_at) })}` : ""}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" onClick={() => pick("password")}>
            {t("change-password")}
          </Button>
          {account.status === "awaiting_code" || flow.authWaiting ? (
            <Button variant="ghost" onClick={() => setCodeOpen((o) => !o)}>
              {t("enter-code")}
            </Button>
          ) : (
            <Button variant="ghost" disabled={flow.resending} onClick={reauth}>
              {t("reauth")}
            </Button>
          )}
          <div ref={menuRef} className="relative">
            <Button variant="ghost" onClick={() => setMenu((m) => !m)}>
              <span aria-label={t("more")}>⋯</span>
            </Button>
            {menu ? (
              <div className="absolute top-full right-0 z-20 mt-1 grid min-w-[140px] gap-1 rounded-[10px] border-2 border-ink bg-white p-1">
                <Button variant="ghost" className="w-full justify-start" onClick={() => pick("name")}>
                  {t("rename")}
                </Button>
                <Button
                  variant="ghost"
                  className="w-full justify-start"
                  onClick={() => {
                    setMenu(false);
                    refresh.mutate(account.id);
                  }}
                >
                  {t("refresh-libs")}
                </Button>
                <Button
                  variant="danger"
                  className="w-full justify-start"
                  onClick={() => {
                    setMenu(false);
                    onDelete(account);
                  }}
                >
                  {t("delete")}
                </Button>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {editing === "name" ? (
        <form className="mt-2" onKeyDownCapture={guardImeSubmit} onSubmit={saveName}>
          <div className="flex flex-wrap gap-2">
            <input
              className={`${INPUT} flex-1`}
              aria-label={t("display-name")}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <Button variant="primary" type="submit" disabled={patch.isPending}>
              {t("confirm")}
            </Button>
            <Button variant="ghost" onClick={() => setEditing("none")}>
              {t("cancel")}
            </Button>
          </div>
          {nameError ? (
            <div role="alert" className="mt-1 text-[12px] text-[#9a3412]">
              {nameError}
            </div>
          ) : null}
        </form>
      ) : null}

      {editing === "password" ? (
        <form className="mt-2" onKeyDownCapture={guardImeSubmit} onSubmit={savePassword}>
          <input
            className={INPUT}
            type="password"
            autoComplete="off"
            aria-label={t("new-password")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <div className="mt-2 flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={() => setEditing("none")}>
              {t("cancel")}
            </Button>
            <Button variant="primary" type="submit" disabled={patch.isPending || password === ""}>
              {t("confirm")}
            </Button>
          </div>
        </form>
      ) : null}

      {codeOpen ? (
        <div className="mt-2">
          <div className="text-[13px] text-[#374151]">{t("mfa-sent")}</div>
          <CodeInput onSubmit={flow.sendCode} error={flow.error} attempt={flow.attempt} pending={flow.pending} />
        </div>
      ) : null}

      {actionError ? (
        <div role="alert" className="mt-2 text-[12px] text-[#991b1b]">
          {actionError}
        </div>
      ) : null}
    </article>
  );
}
