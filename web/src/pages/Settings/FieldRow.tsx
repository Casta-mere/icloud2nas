import { useT } from "../../i18n";
import { FormRow } from "../../ui/FormRow";
import { Toggle } from "../../ui/Toggle";
import type { Field } from "./schema";

const CONTROL = "w-full rounded-[10px] border-2 border-ink bg-white px-2.5 py-1.5 text-[13px]";

/** One schema field as a labelled row: control, then hint / warning / error under it. */
export function FieldRow({
  field,
  value,
  onChange,
  error,
  hint,
}: {
  field: Field;
  value: string;
  onChange: (v: string) => void;
  error?: string | null;
  hint?: string;
}) {
  const t = useT();
  const mono = field.mono ? " font-mono" : "";
  const warn = field.warnKey ? (
    <span className="text-[#991b1b]">{t(field.warnKey)}</span>
  ) : (
    (hint ?? (field.hintKey ? t(field.hintKey) : undefined))
  );
  let control;
  if (field.kind === "toggle") {
    control = <Toggle checked={value === "1"} onChange={(c) => onChange(c ? "1" : "0")} />;
  } else if (field.kind === "textarea") {
    control = (
      <textarea rows={3} value={value} onChange={(e) => onChange(e.target.value)} className={CONTROL + mono} />
    );
  } else if (field.kind === "select") {
    control = (
      <select value={value} onChange={(e) => onChange(e.target.value)} className={CONTROL}>
        {field.options?.map((o) => (
          <option key={o} value={o}>
            {t(`opt-${o}` as Parameters<typeof t>[0])}
          </option>
        ))}
      </select>
    );
  } else {
    control = (
      <input
        type="text"
        inputMode={field.kind === "int" ? "numeric" : field.kind === "float" ? "decimal" : undefined}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={CONTROL + mono + (field.kind === "int" || field.kind === "float" ? " max-w-[8rem]" : "")}
      />
    );
  }
  return (
    <FormRow label={t(field.labelKey)} alignTop={field.kind === "textarea"} hint={warn} error={error ?? undefined}>
      {control}
    </FormRow>
  );
}
