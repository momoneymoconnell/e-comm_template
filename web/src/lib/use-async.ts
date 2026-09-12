"use client";

/**
 * A small data-fetching hook, used by every client-rendered admin and account
 * page.
 *
 * It exists for two reasons.
 *
 * **Consistency.** Seven pages were each hand-rolling loading flags, error
 * state, and a guard against a stale response overwriting a fresh one. That is
 * seven chances to get the race wrong.
 *
 * **Correctness under React 19.** The obvious pattern —
 * `useEffect(() => { setLoading(true); fetch()... })` — sets state
 * synchronously inside an effect, which React 19 flags because it triggers a
 * cascading second render on mount. Here, state is only ever set from a
 * promise continuation, which is the supported place to do it. `loading` is
 * *derived* rather than stored, so nothing has to be set up front.
 *
 * On a refetch (a filter or page change) the previous data stays on screen
 * until the new data arrives, with `stale` set. That avoids the layout jump of
 * collapsing a full table back to a skeleton on every keystroke.
 */

import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";

import { ApiError } from "./api";

interface AsyncState<T> {
  /** The most recent successful result, or null before the first one. */
  data: T | null;
  /** A user-safe message from the most recent failure, or null. */
  error: string | null;
  /** True until the first result (or error) arrives. */
  loading: boolean;
  /** True while a refetch is in flight and `data` is from a previous request. */
  stale: boolean;
  /** Re-run the fetch, e.g. after a mutation. */
  reload: () => void;
}

/** Reduce an unknown thrown value to a message safe to show a user. */
function toMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong. Please try again.";
}

/**
 * Fetch data, re-fetching whenever `deps` change.
 *
 * @param fetcher - Performs the request. Recreated on every render is fine;
 *   `deps` decides when it actually re-runs.
 * @param deps - Values that should trigger a refetch when they change.
 * @returns Data, error and loading state, plus a `reload` function.
 */
export function useAsyncData<T>(fetcher: () => Promise<T>, deps: DependencyList): AsyncState<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null; token: number }>({
    data: null,
    error: null,
    token: -1,
  });
  const [token, setToken] = useState(0);

  // Held in a ref so a changing `fetcher` identity does not itself re-trigger
  // the effect. Callers write inline arrow functions, which are new on every
  // render; `deps` is the intended trigger.
  const fetcherRef = useRef(fetcher);

  // Updated in an effect rather than during render. Writing to a ref while
  // rendering is unsafe under concurrent React, where a render can be
  // discarded and replayed — the mutation would survive a render that never
  // committed. Declared BEFORE the fetching effect so it has already run by
  // the time that one fires.
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    let cancelled = false;

    void fetcherRef
      .current()
      .then((data) => {
        // The cancelled check is what prevents a slow first request from
        // landing after a fast second one and showing the wrong page of
        // results — the classic out-of-order response bug.
        if (!cancelled) setState({ data, error: null, token });
      })
      .catch((error: unknown) => {
        if (!cancelled) setState({ data: null, error: toMessage(error), token });
      });

    return () => {
      cancelled = true;
    };
    // `token` forces a re-run on reload(); the caller's deps do the rest.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, ...deps]);

  const reload = useCallback(() => setToken((n) => n + 1), []);

  // Derived, not stored: we are loading whenever no result has arrived for the
  // request currently in flight.
  const settled = state.token === token;
  return {
    data: state.data,
    error: settled ? state.error : null,
    loading: state.data === null && !settled,
    stale: state.data !== null && !settled,
    reload,
  };
}
