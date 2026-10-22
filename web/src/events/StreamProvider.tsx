import type { ReactNode } from "react";
import { StreamContext } from "./StreamContext";
import { useEventStream } from "./useEventStream";

/** Mounts the one event stream and shares its connection state. */
export function StreamProvider({ children }: { children: ReactNode }) {
  const state = useEventStream();
  return <StreamContext.Provider value={state}>{children}</StreamContext.Provider>;
}
