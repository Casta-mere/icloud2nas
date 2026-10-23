import type { Library } from "../api/types";
import type { Key } from "../i18n";
import type { ChipTone } from "../ui/Chip";

/** The header chip of a library, shared by 总览 and 图库 so the two pages cannot drift. */
export function libraryChip(
  library: Library,
  running: boolean,
  hasPendingPlan: boolean,
): { tone: ChipTone; key: Key } {
  if (library.status === "dir_missing") return { tone: "fuse", key: "lib-dir-missing" };
  if (running) return { tone: "ok", key: "lib-running" };
  if (library.status === "paused") return { tone: "neutral", key: "lib-paused" };
  if (library.status === "space_paused") return { tone: "warn", key: "lib-space-paused" };
  if (hasPendingPlan) return { tone: "fuse", key: "lib-plan-pending" };
  return { tone: "ok", key: "status-ok" };
}
