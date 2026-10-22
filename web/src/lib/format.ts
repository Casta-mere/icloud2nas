import { t } from "../i18n";

const pad = (n: number) => String(n).padStart(2, "0");
const parse = (iso: string | null | undefined): Date | null => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
};

/** Local `HH:MM` (rule 9); "—" when absent. */
export function fmtTime(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : "—";
}

/** Local `YYYY-MM-DD HH:MM` (rule 9); "—" when absent. */
export function fmtDateTime(iso: string | null | undefined): string {
  const d = parse(iso);
  if (!d) return "—";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${fmtTime(iso)}`;
}

const UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** Binary units, one decimal from KB up: `3.1 GB`. */
export function fmtBytes(n: number): string {
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${value} B` : `${value.toFixed(1)} ${UNITS[unit]}`;
}

/** Elapsed time between two instants; a missing end means "until now". */
export function fmtDuration(startIso: string, endIso: string | null | undefined): string {
  const start = parse(startIso);
  if (!start) return "—";
  const end = parse(endIso) ?? new Date();
  const seconds = Math.max(0, Math.round((end.getTime() - start.getTime()) / 1000));
  if (seconds < 60) return t("duration-s", { n: seconds });
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("duration-m", { n: minutes });
  return t("duration-hm", { h: Math.floor(minutes / 60), m: pad(minutes % 60) });
}
