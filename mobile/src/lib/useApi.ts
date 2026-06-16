// Minimal data-fetching hook — the mobile stand-in for SWR on the web.
//
// useApi(fetcher, deps) runs the fetcher, exposes { data, error, isLoading,
// refetch }, cancels stale responses on dep change/unmount, and supports
// pull-to-refresh via refetch(). Keep it tiny and dependency-free.

import { useCallback, useEffect, useRef, useState } from "react";

export type ApiState<T> = {
  data: T | undefined;
  error: Error | undefined;
  isLoading: boolean;
  isRefetching: boolean;
  refetch: () => Promise<void>;
};

export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: ReadonlyArray<unknown> = [],
  opts: { enabled?: boolean } = {},
): ApiState<T> {
  const enabled = opts.enabled ?? true;
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<Error | undefined>(undefined);
  const [isLoading, setIsLoading] = useState<boolean>(enabled);
  const [isRefetching, setIsRefetching] = useState(false);

  const reqId = useRef(0);
  // Keep the latest fetcher without making it a dependency (callers pass inline
  // closures). Deps array is the cache key, mirroring SWR's key semantics.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const run = useCallback(
    async (isRefresh: boolean) => {
      if (!enabled) return;
      const id = ++reqId.current;
      if (isRefresh) setIsRefetching(true);
      else setIsLoading(true);
      try {
        const result = await fetcherRef.current();
        if (id === reqId.current) {
          setData(result);
          setError(undefined);
        }
      } catch (e) {
        if (id === reqId.current) {
          setError(e instanceof Error ? e : new Error(String(e)));
        }
      } finally {
        if (id === reqId.current) {
          setIsLoading(false);
          setIsRefetching(false);
        }
      }
    },
    [enabled],
  );

  useEffect(() => {
    if (!enabled) {
      setIsLoading(false);
      return;
    }
    run(false);
    return () => {
      // Invalidate any in-flight request on dep change / unmount.
      reqId.current++;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);

  const refetch = useCallback(() => run(true), [run]);

  return { data, error, isLoading, isRefetching, refetch };
}
