import { useEffect, type CSSProperties, type ReactNode } from "react";
import { useT } from "../i18n";

/** Right-hand panel, 640px by default and full-screen on phones (rule 8). */
export function Drawer({
  open,
  onClose,
  title,
  children,
  width = 640,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  width?: number;
}) {
  const t = useT();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-[rgba(238,241,246,.55)] backdrop-blur-[1px]" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        style={{ "--drawer-w": `${width}px` } as CSSProperties}
        className="absolute inset-y-0 right-0 flex w-[var(--drawer-w)] max-w-full flex-col border-l-2 border-ink bg-[rgba(255,255,255,.92)] px-[18px] py-4 shadow-[-6px_0_0_rgba(17,17,17,.9)] backdrop-blur-[14px] max-sm:w-full max-sm:border-l-0 max-sm:shadow-none"
      >
        <div className="mb-2.5 flex min-w-0 items-center gap-2.5">
          <div className="min-w-0 flex-1 truncate font-bold">{title}</div>
          <button
            type="button"
            aria-label={t("close")}
            onClick={onClose}
            className="flex-none cursor-pointer rounded-[10px] border-2 border-[#cbd5e1] bg-white px-2 py-0.5 text-[13px] font-semibold active:translate-x-[2px] active:translate-y-[2px]"
          >
            ✕
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">{children}</div>
      </div>
    </div>
  );
}
