"use client";
// One GET bound to component state: the response is set only in the promise callback (never
// synchronously in the effect), a stale response after unmount or a path change is dropped, and
// reload() refetches after a mutation.
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

export interface Loaded<T> { data: T | null; error: ApiError | null; reload: () => void }

export function useApi<T>(path: string | null): Loaded<T> {
  const [state, setState] = useState<{ data: T | null; error: ApiError | null }>({ data: null, error: null });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (path === null) return;
    let alive = true;
    api<T>("GET", path)
      .then((data) => { if (alive) setState({ data, error: null }); })
      .catch((e: unknown) => { if (alive) setState({ data: null, error: e instanceof ApiError ? e : new ApiError(0, "network", "unavailable") }); });
    return () => { alive = false; };
  }, [path, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}
