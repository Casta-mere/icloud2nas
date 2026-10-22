import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

/**
 * One row of a card's 96px label column (rule 1). Hints and errors go under the control
 * in the second column, never beside it (rule 2); multi-line controls pass `alignTop` (rule 5).
 * The label names the single child control (a generated id unless it has one); `htmlFor` overrides.
 */
export function FormRow({
  label,
  hint,
  error,
  children,
  alignTop = false,
  htmlFor,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  alignTop?: boolean;
  htmlFor?: string;
}) {
  const generated = useId();
  let control = children;
  let target = htmlFor;
  if (target === undefined && isValidElement<{ id?: string }>(children)) {
    target = children.props.id ?? generated;
    if (children.props.id === undefined) {
      control = cloneElement(children as ReactElement<{ id?: string }>, { id: generated });
    }
  }
  return (
    <div
      className={`grid grid-cols-[96px_1fr] gap-x-3.5 gap-y-1 ${alignTop ? "items-start" : "items-center"}`}
    >
      <label
        htmlFor={target}
        className={`truncate text-[13px] text-[#6b7280] ${alignTop ? "pt-1.5" : ""}`}
      >
        {label}
      </label>
      <div className="min-w-0">{control}</div>
      {hint !== undefined && (
        <div className="col-start-2 min-w-0 text-xs text-[#6b7280]">{hint}</div>
      )}
      {error !== undefined && error !== null && error !== "" && (
        <div role="alert" className="col-start-2 min-w-0 text-xs text-[#9a3412]">
          {error}
        </div>
      )}
    </div>
  );
}
