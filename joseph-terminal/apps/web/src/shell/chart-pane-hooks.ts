/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Chart pane hooks for comparison overlays and session
 * separators. Each hook owns its subscriptions and removes what it added
 * when the inputs change. Studies (including the Delta % pane) are drawn by
 * `useStudyRuns`.
 */

'use client';
import {
  createSessionSeparatorLayer,
  themeFromCss,
  type ChartBar,
  type ChartPort,
  type CompareSeriesSpec,
} from '@joseph/chart-core';
import type {ChartState, InstrumentId} from '@joseph/contracts';
import {useEffect, useState, type MutableRefObject} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {toChartBar} from './chart-bars';

/** Refresh interval of live comparison data (30 s). */
const REFRESH_MS = 30_000;

/** Line colors of compared instruments (distinct from bid/ask/course). */
export const COMPARE_COLORS: readonly string[] = [
  '#f2b544',
  '#5ab0f0',
  '#b49cff',
];

/** Values by bar start time (UTC ms). */
export type ValueByTime = ReadonlyMap<number, number>;

/**
 * Reads a CSS custom property from the document root.
 * @param name Property name, e.g. `--chart-text`.
 * @return Computed value; empty when the property is not set.
 */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name);
}

/** One compared instrument with its closes. */
export interface CompareEntry {
  /** Instrument id. */
  instrumentId: InstrumentId;
  /** Line color, from `COMPARE_COLORS`. */
  color: string;
  /** Closes by bar start. */
  values: ValueByTime;
  /** Why candles could not be loaded, or null when they were. */
  error: string | null;
}

/**
 * Loads comparison candles every 30 s and overlays them on the chart. A
 * failed instrument keeps its entry with the error and is left off the
 * chart.
 * @param port Chart port (null before mount).
 * @param chart Chart state; `compare` lists the instruments.
 * @param runtime Runtime whose market client is asked.
 * @param labels Returns the display symbol of an instrument id.
 * @return Entries for the legend and data window.
 */
export function useCompare(
  port: ChartPort | null,
  chart: ChartState,
  runtime: TerminalRuntime,
  labels: (instrumentId: InstrumentId) => string
): readonly CompareEntry[] {
  const ids = chart.compare ?? [];
  const key = ids.join('|');
  const {timeframe} = chart;
  const [entries, setEntries] = useState<readonly CompareEntry[]>([]);
  useEffect(() => {
    if (!port) return;
    const list = key ? key.split('|') : [];
    if (list.length === 0) {
      port.setCompareSeries([]);
      setEntries([]);
      return;
    }
    let alive = true;
    const load = () => {
      void Promise.allSettled(
        list.map(id =>
          runtime.clients.market.getCandles({
            instrumentId: id,
            timeframe,
            limit: 500,
          })
        )
      ).then(results => {
        if (!alive) return;
        const next: CompareEntry[] = results.map((result, i) => {
          // `results` lines up with `list`, the split `chart.compare` ids.
          const instrumentId = list[i] as InstrumentId;
          const color = COMPARE_COLORS[i % COMPARE_COLORS.length] ?? '#f2b544';
          if (result.status === 'rejected') {
            return {
              instrumentId,
              color,
              values: new Map(),
              error:
                result.reason instanceof Error
                  ? result.reason.message
                  : 'Candles unavailable',
            };
          }
          const bars: ChartBar[] = result.value.candles.map(toChartBar);
          return {
            instrumentId,
            color,
            values: new Map(bars.map(b => [b.timeMs, b.close])),
            error: null,
          };
        });
        const specs: CompareSeriesSpec[] = next
          .filter(entry => entry.error === null)
          .map(entry => ({
            id: entry.instrumentId,
            label: labels(entry.instrumentId),
            color: entry.color,
            points: [...entry.values].map(([timeMs, value]) => ({
              timeMs,
              value,
            })),
          }));
        port.setCompareSeries(specs);
        setEntries(next);
      });
    };
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `labels` is read once per load; a new labels function must not restart the polling
  }, [port, key, timeframe, runtime]);
  return entries;
}

/**
 * Draws session separators while the chart option is on: UTC calendar
 * boundaries for 24x7 markets, every trading session (in exchange time)
 * for session markets.
 * @param port Chart port (null before mount).
 * @param enabled Whether separators are shown.
 * @param bars Bars currently displayed (read on every repaint).
 * @param timeframe Chart timeframe.
 * @param timeZone Exchange time zone (`UTC` for 24x7 markets).
 */
export function useSessionSeparators(
  port: ChartPort | null,
  enabled: boolean,
  bars: MutableRefObject<readonly ChartBar[]>,
  timeframe: ChartState['timeframe'],
  timeZone = 'UTC'
): void {
  useEffect(() => {
    if (!port || !enabled) return;
    const theme = themeFromCss(cssVar);
    return port.addCanvasLayer(
      createSessionSeparatorLayer({
        times: () => bars.current.map(bar => bar.timeMs),
        timeframe: () => timeframe,
        timeZone: () => timeZone,
        color: () => theme.text,
        fontFamily: () => theme.fontFamily,
      })
    );
  }, [port, enabled, bars, timeframe, timeZone]);
}
