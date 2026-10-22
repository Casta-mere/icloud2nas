import { useState } from "react";

/** Local edit state seeded from a server value and re-seeded whenever that value changes. */
export function useSeeded(server: string): [string, (v: string) => void] {
  const [value, setValue] = useState(server);
  const [seen, setSeen] = useState(server);
  if (seen !== server) {
    setSeen(server);
    setValue(server);
  }
  return [value, setValue];
}
