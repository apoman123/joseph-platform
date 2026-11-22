/**
 * @fileoverview Chart pane hooks for backend study panes (order-flow
 * Delta %), comparison overlays and session separators. Each hook owns its
 * subscriptions and removes what it added when the inputs change.
 */
'use client';
import {
  createSessionSeparatorLayer,
  themeFromCss,
  type ChartBar,
  type ChartPort,
  type CompareSeriesSpec,
} from '@joseph/chart-core';
import type {
  ChartState,
  IndicatorSeries,
  InstrumentId,
} from '@joseph/contracts';
import {
  DELTA_STUDY_ID,
  deltaBarsFromSeries,
  deltaStudyFromSeries,
  type DeltaBar,
} from '@joseph/indicator-viz';
import {layerSettings, layerStyle, layerVisible} from '@joseph/terminal-shell';
import {useEffect, useMemo, useState, type MutableRefObject} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {toChartBar} from './chart-bars';

/** Refresh interval of study and comparison data. */
const REFRESH_MS = 30_000;

/** Line colors of compared instruments (distinct from bid/ask/course). */
export const COMPARE_COLORS: readonly string[] = [
  '#f2b544',
  '#5ab0f0',
  '#b49cff',
];

/** Values by bar start time. */
export type ValueByTime = ReadonlyMap<number, number>;

/**
 * Reads a CSS custom property from the document root.
 * @param name Property name.
 * @return Value.
 */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name);
}

/** Engine delta bars by bar start time. */
export type DeltaByTime = ReadonlyMap<number, DeltaBar>;

/** State of the Delta % pane. */
export interface DeltaPaneState {
  /** Whether the pane is requested by the chart's settings. */
  shown: boolean;
  /** Delta %, method (true/estimate) and volumes by bar start. */
  bars: DeltaByTime;
  /** Why the series is unavailable, if it is. */
  error: string | null;
}

/**
 * Whether a chart shows the order-flow Delta % pane.
 * @param chart Chart state.
 * @return True when the layer is visible and its pane is not hidden.
 */
export function deltaPaneShown(chart: ChartState): boolean {
  return (
    layerVisible(chart, 'order_flow_pressure') &&
    layerStyle(chart, 'order_flow_pressure').pane !== false
  );
}

/**
 * Keeps the order-flow Delta % pane in sync with `/v1/indicators/series`.
 * A failed request shows an empty pane that says why, never invented bars.
 * @param port Chart port (null before mount).
 * @param chart Chart state.
 * @param runtime Runtime.
 * @param atMs Replay point-in-time, or null when live.
 * @return Pane state for the data window.
 */
export function useDeltaPane(
  port: ChartPort | null,
  chart: ChartState,
  runtime: TerminalRuntime,
  atMs: number | null
): DeltaPaneState {
  const shown = deltaPaneShown(chart);
  const settingsKey = JSON.stringify(
    layerSettings(chart, 'order_flow_pressure')
  );
  const [series, setSeries] = useState<IndicatorSeries | null>(null);
  const [error, setError] = useState<string | null>(null);
  const {instrument_id: instrumentId, timeframe} = chart;
  const live = atMs === null;
  // Fetch: values are point-in-time per bar, so replay reuses one fetch.
  useEffect(() => {
    setSeries(null);
    setError(null);
    if (!shown) return;
    let alive = true;
    const inputs = JSON.parse(settingsKey) as Record<string, unknown> | null;
    const load = () => {
      runtime.clients.market
        .getIndicatorSeries({
          instrumentId,
          timeframe,
          pluginId: 'order_flow_pressure',
          ...(inputs ? {settings: inputs} : {}),
        })
        .then(next => {
          if (!alive) return;
          setSeries(next);
          setError(null);
        })
        .catch((failure: unknown) => {
          if (!alive) return;
          setError(
            failure instanceof Error ? failure.message : 'request failed'
          );
        });
    };
    load();
    const timer = live ? setInterval(load, REFRESH_MS) : null;
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [shown, instrumentId, timeframe, settingsKey, live, runtime]);
  // Render: apply the replay cursor and thresholds to the latest series.
  const spec = useMemo(() => {
    if (!shown) return null;
    if (series && series.instrument_id === instrumentId) {
      return deltaStudyFromSeries(series, {
        settings: JSON.parse(settingsKey) as Record<string, unknown> | null,
        atMs,
      });
    }
    if (error) {
      return {
        id: DELTA_STUDY_ID,
        title: 'Delta %',
        style: 'histogram' as const,
        points: [],
        caption: `unavailable: ${error}`,
      };
    }
    return null;
  }, [shown, series, error, instrumentId, settingsKey, atMs]);
  useEffect(() => {
    if (!port) return;
    if (spec) port.setStudyPane(spec);
    else port.removeStudyPane(DELTA_STUDY_ID);
  }, [port, spec]);
  useEffect(
    () => () => {
      port?.removeStudyPane(DELTA_STUDY_ID);
    },
    [port]
  );
  const bars = useMemo(
    () =>
      new Map(
        shown && series && series.instrument_id === instrumentId
          ? deltaBarsFromSeries(series, atMs).map(bar => [bar.timeMs, bar])
          : []
      ),
    [shown, series, instrumentId, atMs]
  );
  return {shown, bars, error};
}

/** One compared instrument with its latest close change. */
export interface CompareEntry {
  /** Instrument id. */
  instrumentId: InstrumentId;
  /** Line color. */
  color: string;
  /** Closes by bar start. */
  values: ValueByTime;
  /** Error when candles could not be loaded. */
  error: string | null;
}

/**
 * Loads comparison candles and overlays them on the chart.
 * @param port Chart port.
 * @param chart Chart state.
 * @param runtime Runtime.
 * @param labels Display symbol per instrument id.
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
    const list = key ? (key.split('|') as InstrumentId[]) : [];
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
    // `labels` is read once per load; the legend re-renders separately.
  }, [port, key, timeframe, runtime]); // eslint-disable-line react-hooks/exhaustive-deps
  return entries;
}

/**
 * Draws UTC session separators while the chart option is on.
 * @param port Chart port.
 * @param enabled Whether separators are shown.
 * @param bars Bars currently displayed.
 * @param timeframe Chart timeframe accessor.
 */
export function useSessionSeparators(
  port: ChartPort | null,
  enabled: boolean,
  bars: MutableRefObject<readonly ChartBar[]>,
  timeframe: ChartState['timeframe']
): void {
  useEffect(() => {
    if (!port || !enabled) return;
    const theme = themeFromCss(cssVar);
    return port.addCanvasLayer(
      createSessionSeparatorLayer({
        times: () => bars.current.map(bar => bar.timeMs),
        timeframe: () => timeframe,
        color: () => theme.text,
        fontFamily: () => theme.fontFamily,
      })
    );
  }, [port, enabled, bars, timeframe]);
}
