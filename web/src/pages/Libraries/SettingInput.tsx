import { useId, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../../api/client";
import { isImeEnter } from "../../lib/ime";
import { useSeeded } from "../../lib/useSeeded";
import { FormRow } from "../../ui/FormRow";

/**
 * One inline setting: seeded from the server value, committed on blur or Enter only when it
 * changed. `onCommit` resolves with the saved value; a rejection reverts the field, and an
 * `ApiError`'s `detail` is shown under it.
 */
export function SettingInput({
  label,
  hint,
  server,
  onCommit,
  type = "text",
  inputMode,
  min,
  max,
  disabled = false,
  width = "w-full",
  suffix,
}: {
  label: ReactNode;
  hint?: ReactNode;
  server: string;
  onCommit: (value: string) => Promise<string>;
  type?: "text" | "number";
  inputMode?: "numeric";
  min?: number;
  max?: number;
  disabled?: boolean;
  width?: string;
  suffix?: ReactNode;
}) {
  const id = useId();
  const [value, setValue] = useSeeded(server);
  const [error, setError] = useState<string | null>(null);
  const base = useRef(server);
  const busy = useRef(false);
  const lastServer = useRef(server);
  if (lastServer.current !== server) {
    lastServer.current = server;
    base.current = server;
  }

  const commit = async () => {
    const next = value.trim();
    if (busy.current || next === base.current) {
      if (next === base.current) setValue(base.current);
      return;
    }
    busy.current = true;
    setError(null);
    try {
      const saved = await onCommit(next);
      base.current = saved;
      setValue(saved);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : null);
      setValue(base.current);
    } finally {
      busy.current = false;
    }
  };

  return (
    <FormRow label={label} htmlFor={id} hint={hint} error={error}>
      <div className="flex min-w-0 items-center gap-2 text-[13px]">
        <input
          id={id}
          type={type}
          inputMode={inputMode}
          min={min}
          max={max}
          value={value}
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
          onBlur={() => void commit()}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !isImeEnter(e)) void commit();
          }}
          className={`${width} min-w-0 rounded-[10px] border-2 border-ink bg-white px-2.5 py-1.5 text-[13px] tabular-nums disabled:opacity-50`}
        />
        {suffix}
      </div>
    </FormRow>
  );
}
