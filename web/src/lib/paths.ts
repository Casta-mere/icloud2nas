/** Mirrors the backend's `validate_target_dir` (without the nesting check) and `safe_dir_name`. */

const RESERVED_TOP = ["kept"];

const components = (path: string): string[] => path.split("/").filter((c) => c !== "");

/** A photos-relative directory in canonical form, or `null` when the server would refuse it. */
export function normalizeRel(input: string): string | null {
  let rel = input.trim();
  if (rel === "" || rel.startsWith("/")) return null;
  rel = rel.replace(/\/+/g, "/");
  while (rel.startsWith("./")) rel = rel.slice(2);
  rel = rel.replace(/\/+$/, "");
  const parts = components(rel);
  if (parts.length === 0) return null;
  if (parts.some((p) => p.startsWith("."))) return null;
  const first = parts[0] ?? "";
  if (RESERVED_TOP.includes(first) || first === "shared") return null;
  return parts.join("/");
}

/** True when the two directories are equal or one is an ancestor of the other. */
export function nests(a: string, b: string): boolean {
  const pa = components(a);
  const pb = components(b);
  const shorter = Math.min(pa.length, pb.length);
  return pa.slice(0, shorter).join("/") === pb.slice(0, shorter).join("/");
}

/** The folder name the backend derives from a display name; `""` when nothing usable is left. */
export function defaultDir(displayName: string): string {
  return displayName.trim().replace(/[\\/]/g, "_").replace(/^\.+/, "").trim();
}
