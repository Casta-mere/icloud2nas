import { useEffect, useRef, useState, type FormEvent } from "react";
import { isImeEnter } from "../lib/ime";
import { useT } from "../i18n";
import { Button } from "./Button";

/**
 * Six-digit verification-code field (phone-first: numeric keypad, one-time-code autofill).
 * Submits on Enter or when the sixth digit is typed. Bump `attempt` after a wrong code to
 * clear the field and put the caret back in it; `error` is shown under the field.
 */
export function CodeInput({
  onSubmit,
  error,
  attempt = 0,
  pending = false,
}: {
  onSubmit: (code: string) => void;
  error?: string | null;
  attempt?: number;
  pending?: boolean;
}) {
  const t = useT();
  const [value, setValue] = useState("");
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (attempt === 0) return;
    setValue("");
    ref.current?.focus();
  }, [attempt]);

  const submit = (code: string) => {
    if (code.length === 6 && !pending) onSubmit(code);
  };

  const onChange = (raw: string) => {
    const digits = raw.replace(/\D/g, "").slice(0, 6);
    setValue(digits);
    submit(digits);
  };

  const onFormSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit(value);
  };

  return (
    <form onSubmit={onFormSubmit} className="mt-3">
      <div className="flex gap-2">
        <input
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            // an Enter that confirms an IME composition must not submit the form
            if (isImeEnter(e)) e.preventDefault();
          }}
          inputMode="numeric"
          pattern="[0-9]*"
          maxLength={6}
          autoComplete="one-time-code"
          aria-label={t("code-label")}
          aria-invalid={error ? true : undefined}
          placeholder="······"
          className="min-w-0 flex-1 rounded-[10px] border-2 border-ink bg-white px-3 py-2.5 font-mono text-[22px] tracking-[.35em] tabular-nums"
        />
        <Button type="submit" variant="primary" disabled={pending || value.length < 6}>
          {t("code-submit")}
        </Button>
      </div>
      {error ? (
        <div role="alert" className="mt-1.5 text-[12px] text-[#991b1b]">
          {error}
        </div>
      ) : null}
    </form>
  );
}
