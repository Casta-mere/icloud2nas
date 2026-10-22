import type { ReactNode } from "react";

/** The style-A switch (34×18, ink border); left-aligned with its label text (rule 5). */
export function Toggle({
  checked,
  onChange,
  disabled = false,
  label,
  id,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label?: ReactNode;
  id?: string;
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="inline-flex cursor-pointer items-center gap-2 text-left text-[13px] text-[#374151] disabled:cursor-not-allowed disabled:opacity-50"
    >
      <span
        aria-hidden="true"
        className={`relative inline-block h-[18px] w-[34px] flex-none rounded-full border-2 border-ink ${checked ? "bg-ink" : "bg-white"}`}
      >
        <span
          className={`absolute top-px h-3 w-3 rounded-full ${checked ? "right-0.5 bg-white" : "left-0.5 bg-ink"}`}
        />
      </span>
      {label !== undefined && <span>{label}</span>}
    </button>
  );
}
