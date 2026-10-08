"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { operatorRequest, type OperatorRequestOptions } from "@/lib/operator-api";
import type { OperatorOperation, ReplyOf } from "@/lib/gen/api.gen";

export interface OperatorLoaded<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

export function useOperatorApi<K extends OperatorOperation>(
  key: K,
  options: OperatorRequestOptions = {},
  enabled = true,
): OperatorLoaded<ReplyOf<K>> {
  const optionsKey = JSON.stringify(options);
  const stableOptions = useMemo(() => JSON.parse(optionsKey) as OperatorRequestOptions, [optionsKey]);
  const [version, setVersion] = useState(0);
  const [state, setState] = useState<{
    requestKey: string | null;
    data: ReplyOf<K> | null;
    error: ApiError | null;
  }>({ requestKey: null, data: null, error: null });
  const requestKey = key + "\u0000" + optionsKey + "\u0000" + version;

  useEffect(() => {
    if (!enabled) return;
    let current = true;
    operatorRequest(key, stableOptions)
      .then((data) => { if (current) setState({ requestKey, data, error: null }); })
      .catch((error: unknown) => {
        if (!current) return;
        setState({
          requestKey,
          data: null,
          error: error instanceof ApiError ? error : new ApiError(0, "network", "The operator API is unavailable."),
        });
      });
    return () => { current = false; };
  }, [enabled, key, requestKey, stableOptions]);

  const reload = useCallback(() => setVersion((value) => value + 1), []);
  const matched = enabled && state.requestKey === requestKey;
  return {
    data: matched ? state.data : null,
    error: matched ? state.error : null,
    loading: enabled && !matched,
    reload,
  };
}
