/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Heatmap data loading: the engine's `GET /v1/heatmap` and
 * catalogue, or the synthetic DEMO generator without engines.
 *
 * A snapshot refreshes every 60 s; while the engine reports `pending`
 * (a slow source still loading) it polls again after 3 s.
 */

'use client';
import {
  PythonClient,
  type HeatmapCatalog,
  type HeatmapSnapshot,
} from '@joseph/contracts';
import {demoHeatmapCatalog, demoHeatmapSnapshot} from '@joseph/mock-provider';
import {useCallback, useEffect, useMemo, useState} from 'react';
import type {TerminalRuntime} from '../../composition/runtime';
import type {HeatmapType} from './model';

/** Regular refresh interval. */
export const REFRESH_MS = 60_000;

/** Poll interval while the engine is still loading a source. */
export const PENDING_MS = 3_000;

/** State of the heatmap data. */
export interface HeatmapData {
  /** Data set catalogue, or null until loaded. */
  catalog: HeatmapCatalog | null;
  /** Latest snapshot of the selected data set, or null while loading. */
  snapshot: HeatmapSnapshot | null;
  /** Latest load error, or null. */
  error: string | null;
  /** Whether a load is in flight. */
  loading: boolean;
  /** When the next automatic refresh runs (epoch ms), or null. */
  nextRefreshMs: number | null;
  /** Reloads now. */
  refresh: () => void;
}

/**
 * Loads and refreshes a heatmap data set.
 * @param runtime Terminal runtime (DEMO or engine configuration).
 * @param type Heatmap family.
 * @param dataset Data set id, or null for the family default.
 * @return Data state.
 */
export function useHeatmapData(
  runtime: TerminalRuntime,
  type: HeatmapType,
  dataset: string | null
): HeatmapData {
  const demo = runtime.config.demo;
  const client = useMemo(
    () =>
      new PythonClient({
        baseUrl: runtime.config.marketApiUrl ?? '',
        getToken: () => runtime.config.devToken,
      }),
    [runtime]
  );
  const [catalog, setCatalog] = useState<HeatmapCatalog | null>(null);
  const [state, setState] = useState<{
    key: string;
    snapshot: HeatmapSnapshot | null;
    error: string | null;
    loading: boolean;
    next: number | null;
  }>({key: '', snapshot: null, error: null, loading: true, next: null});
  const [revision, setRevision] = useState(0);
  const key = `${type}|${dataset ?? ''}`;
  useEffect(() => {
    if (demo) {
      setCatalog(demoHeatmapCatalog());
      return undefined;
    }
    const controller = new AbortController();
    client.heatmapCatalog(controller.signal).then(
      value => void setCatalog(value),
      () => undefined
    );
    return () => void controller.abort();
  }, [client, demo]);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const load = async () => {
      setState(prev => ({
        ...prev,
        key,
        snapshot: prev.key === key ? prev.snapshot : null,
        loading: true,
      }));
      let next = REFRESH_MS;
      try {
        const snapshot = demo
          ? demoHeatmapSnapshot(
              {type, dataset: dataset ?? undefined},
              Date.now()
            )
          : await client.heatmap(
              {type, dataset: dataset ?? undefined},
              controller.signal
            );
        if (snapshot.pending) next = PENDING_MS;
        if (!disposed) {
          setState({
            key,
            snapshot,
            error: null,
            loading: false,
            next: Date.now() + next,
          });
        }
      } catch (failure) {
        if (disposed) return;
        setState(prev => ({
          ...prev,
          key,
          error:
            failure instanceof Error
              ? failure.message
              : 'Heatmap data unavailable',
          loading: false,
          next: Date.now() + next,
        }));
      }
      if (!disposed) timer = setTimeout(() => void load(), next);
    };
    void load();
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [client, demo, type, dataset, key, revision]);
  const refresh = useCallback(() => void setRevision(n => n + 1), []);
  const current = state.key === key;
  return {
    catalog,
    snapshot: current ? state.snapshot : null,
    error: current ? state.error : null,
    loading: !current || state.loading,
    nextRefreshMs: current ? state.next : null,
    refresh,
  };
}
