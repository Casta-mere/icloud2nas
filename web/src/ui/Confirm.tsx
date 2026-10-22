import { useEffect, type ReactNode } from "react";
import { useT } from "../i18n";
import { Button } from "./Button";
import { Card } from "./Card";

export function Confirm({
  open,
  title,
  body,
  confirmLabel,
  danger = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: ReactNode;
  body: ReactNode;
  confirmLabel: ReactNode;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const t = useT();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[rgba(238,241,246,.55)] px-4">
      <div role="alertdialog" aria-modal="true" className="w-full max-w-[420px]">
        <Card tone={danger ? "fuse" : "neutral"}>
          <p className="mb-2 truncate text-lg font-extrabold tracking-[-.3px]">{title}</p>
          <div className="text-[13px] text-[#374151]">{body}</div>
          <div className="mt-4 flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onCancel}>
              {t("cancel")}
            </Button>
            <Button variant={danger ? "danger" : "primary"} onClick={onConfirm}>
              {confirmLabel}
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
