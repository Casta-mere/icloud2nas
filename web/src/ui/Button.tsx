import type { ReactNode } from "react";

export type ButtonVariant = "primary" | "default" | "ghost" | "danger";

const VARIANT: Record<ButtonVariant, string> = {
  primary: "border-ink bg-ink text-white shadow-[3px_3px_0_rgba(17,17,17,.35)]",
  default: "border-ink bg-white text-ink shadow-[3px_3px_0_rgba(17,17,17,.35)]",
  ghost: "border-[#cbd5e1] bg-white font-semibold text-ink shadow-none",
  danger: "border-[#991b1b] bg-white text-[#991b1b] shadow-[3px_3px_0_rgba(153,27,27,.3)]",
};

/** Never wraps (rule 3); pressing moves only the button (shadow collapses into the offset). */
export function Button({
  variant = "default",
  type = "button",
  disabled = false,
  onClick,
  selected,
  children,
  className = "",
}: {
  variant?: ButtonVariant;
  type?: "button" | "submit" | "reset";
  disabled?: boolean;
  onClick?: () => void;
  /** Marks the current item of a list of buttons (`data-selected`, `aria-current`). */
  selected?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      data-selected={selected ? "true" : undefined}
      aria-current={selected ? "true" : undefined}
      className={`inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 rounded-[10px] border-2 px-3 py-1.5 text-[13px] font-bold whitespace-nowrap select-none enabled:active:translate-x-[2px] enabled:active:translate-y-[2px] enabled:active:shadow-none disabled:cursor-not-allowed disabled:opacity-50 ${VARIANT[variant]} ${className}`}
    >
      {children}
    </button>
  );
}
