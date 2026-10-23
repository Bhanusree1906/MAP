import { useCallback, useEffect, useRef, useState } from "react";

import { formatError } from "../api/client";

/** Polls fetchFn every intervalMs while enabled is true. Fetches once
 * immediately on mount/dependency change regardless of enabled. Exposes
 * refetch() for callers that need an out-of-cycle refresh (e.g. right
 * after an action that changes state polling had already stopped for). */
export function usePolling<T>(
  fetchFn: () => Promise<T>,
  intervalMs: number,
  enabled: boolean,
  deps: unknown[] = [],
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fetchFnRef = useRef(fetchFn);
  fetchFnRef.current = fetchFn;

  const tick = useCallback(async () => {
    try {
      const result = await fetchFnRef.current();
      setData(result);
      setError(null);
    } catch (err) {
      setError(formatError(err));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    const guardedTick = async () => {
      try {
        const result = await fetchFnRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(formatError(err));
      }
    };

    guardedTick();
    if (!enabled)
      return () => {
        cancelled = true;
      };

    const id = setInterval(guardedTick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, ...deps]);

  return { data, error, refetch: tick };
}
