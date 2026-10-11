import { useEffect, useState } from "react";

/** Re-renders the calling component every `ms` so clocks, "ago" labels and
 * ETA countdowns keep moving even when no new data has arrived. */
export function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}
