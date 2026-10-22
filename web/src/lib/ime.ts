import type { KeyboardEvent } from "react";

/**
 * True for the Enter that confirms an IME composition. Chrome and Firefox keep
 * `isComposing` set on it; Safari has already cleared it (compositionend fires first) but still
 * reports keyCode 229.
 */
export function isImeEnter(e: KeyboardEvent): boolean {
  return e.key === "Enter" && (e.nativeEvent.isComposing || e.keyCode === 229);
}

/** `onKeyDownCapture` for a `<form>`: stops a composing Enter from submitting it natively. */
export function guardImeSubmit(e: KeyboardEvent): void {
  if (isImeEnter(e)) e.preventDefault();
}
