import { createContext, useContext } from "react";

export interface StreamState {
  connected: boolean;
}

export const StreamContext = createContext<StreamState>({ connected: false });

export function useStream(): StreamState {
  return useContext(StreamContext);
}
