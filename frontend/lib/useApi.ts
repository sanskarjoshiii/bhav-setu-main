"use client";

/**
 * The loading/error/data hook every page uses now that data is real.
 *
 * Mock data was synchronous and always succeeded, so pages rendered straight
 * from a constant. Real data can be slow, absent, or refused with a reason —
 * and "refused with a reason" is the interesting case: when the backend says
 * "too little history for mango at Solapur", the page shows that sentence
 * rather than an empty chart or a spinner that never stops.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  /** Re-run the fetch. Used by the "Try again" button in ErrorState. */
  reload: () => void;
}

export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[] = [],
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);

  // A slow request for an old filter must not overwrite a fast one for the
  // current filter — switching crops quickly used to land on the wrong data.
  const latest = useRef(0);

  useEffect(() => {
    const id = ++latest.current;
    let alive = true;
    setLoading(true);
    setError(null);

    fetcher()
      .then((value) => {
        if (!alive || id !== latest.current) return;
        setData(value);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!alive || id !== latest.current) return;
        setError(
          err instanceof ApiError
            ? err
            : new ApiError(0, err instanceof Error ? err.message : "Something went wrong"),
        );
        setData(null);
      })
      .finally(() => {
        if (alive && id === latest.current) setLoading(false);
      });

    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, loading, error, reload };
}
