/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Chart pane hooks for metadata-driven indicator studies:
 * price-pane lines (plots with `pane: 'price'`, such as moving averages)
 * drawn on canvas layers, and study panes below the chart for the panes a
 * plugin declares. Values come from `/v1/indicators/series`; each hook
 * owns its requests and removes what it added when the inputs change. The
 * volume delta pane has its own hook (`useDeltaPane`).
 */

'use client';
import {
  themeFromCss,
  type ChartPort,
  type ChartTheme,
  type StudyPaneSpec,
} from '@joseph/chart-core';
import type {
  ChartState,
  IndicatorPluginInfo,
  IndicatorSeries,
} from '@joseph/contracts';
import {
  DELTA_PLUGIN_ID,
  createPlotLayer,
  panePlots,
  plotLinesFromSeries,
  studyPaneFromSeries,
  studyPaneId,
  type PlotLine,
} from '@joseph/indicator-viz';
import {
  layerSettings,
  paneShown,
  type IndicatorLayerStyle,
} from '@joseph/terminal-shell';
import {useEffect, useMemo, useRef, useState} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {indicatorPlugins} from './indicator-plugins';
import {useStore} from './react-bindings';

/** Refresh interval of live study series (30 s). */
const REFRESH_MS = 30_000;

/** Indicator layers of a chart. */
type ChartLayers = ChartState['indicator_layers'];

/** Latest answer for one plugin's series. */
export interface StudySeriesState {
  /** Series, or null after a failure. */
  series: IndicatorSeries | null;
  /** Why the series is unavailable, if it is. */
  error: string | null;
}

/**
 * Reads a CSS custom property from the document root.
 * @param name Property name, e.g. `--chart-text`.
 * @return Computed value; empty when the property is not set.
 */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name);
}

/**
 * Returns the stored layer of a plugin.
 * @param layers Chart layers.
 * @param pluginId Plugin id.
 * @return Layer, or undefined when the chart does not list it.
 */
function layerOf(
  layers: ChartLayers,
  pluginId: string
): ChartLayers[number] | undefined {
  return layers.find(layer => layer.plugin_id === pluginId);
}

/**
 * Returns the visible layer plugins of a chart that match a filter.
 * @param plugins Plugin catalogue (null before it loads).
 * @param layers Chart layers; an unlisted plugin follows its
 *     `display.default_visible`.
 * @param wanted Selects the plugins the caller draws.
 * @return Matching plugins in registration order.
 */
function visibleStudies(
  plugins: readonly IndicatorPluginInfo[] | null,
  layers: ChartLayers,
  wanted: (info: IndicatorPluginInfo) => boolean
): IndicatorPluginInfo[] {
  return (plugins ?? []).filter(
    info =>
      info.display.layer &&
      (layerOf(layers, info.plugin_id)?.visible ??
        info.display.default_visible) &&
      wanted(info)
  );
}

/**
 * Fetches the series of several plugins for a chart. Live charts refresh
 * every 30 s; a replayed chart fetches once, since values are point-in-time
 * per bar. A change of instrument, timeframe, plugins or inputs starts
 * over with an empty map.
 * @param chart Chart state (instrument, timeframe and layer inputs).
 * @param runtime Runtime whose market client is asked.
 * @param atMs Replay point-in-time (UTC ms), or null when live.
 * @param studies Plugins to fetch.
 * @return Latest answer per plugin id.
 */
function useStudySeries(
  chart: ChartState,
  runtime: TerminalRuntime,
  atMs: number | null,
  studies: readonly IndicatorPluginInfo[]
): ReadonlyMap<string, StudySeriesState> {
  // Serialized so the effect re-runs when plugins or their inputs change,
  // not when the arrays are rebuilt.
  const key = JSON.stringify(
    studies.map(info => [info.plugin_id, layerSettings(chart, info.plugin_id)])
  );
  const {instrument_id: instrumentId, timeframe} = chart;
  const live = atMs === null;
  const [results, setResults] = useState<ReadonlyMap<string, StudySeriesState>>(
    () => new Map()
  );
  useEffect(() => {
    setResults(new Map());
    // The key is the JSON written just above.
    const requests = JSON.parse(key) as Array<
      [string, Record<string, unknown> | null]
    >;
    if (requests.length === 0) return;
    let alive = true;
    /**
     * Records one plugin's answer.
     * @param pluginId Plugin id.
     * @param state Answer.
     */
    function record(pluginId: string, state: StudySeriesState): void {
      if (alive) setResults(previous => new Map(previous).set(pluginId, state));
    }
    /** Requests every plugin's series. */
    function load(): void {
      for (const [pluginId, settings] of requests) {
        runtime.clients.market
          .getIndicatorSeries({
            instrumentId,
            timeframe,
            pluginId,
            ...(settings ? {settings} : {}),
          })
          .then(series => void record(pluginId, {series, error: null}))
          .catch((failure: unknown) => {
            const error =
              failure instanceof Error ? failure.message : 'request failed';
            record(pluginId, {series: null, error});
          });
      }
    }
    load();
    const timer = live ? setInterval(load, REFRESH_MS) : null;
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [key, instrumentId, timeframe, live, runtime]);
  return results;
}

/**
 * Draws the price-pane lines of every visible plugin that declares
 * `pane: 'price'` plots, one canvas layer per plugin, painted in
 * `display.z_index` order above session breaks and below drawings.
 * @param port Chart port (null before mount).
 * @param chart Chart state.
 * @param runtime Runtime whose plugin catalogue and market client are used.
 * @param atMs Replay point-in-time (UTC ms), or null when live.
 */
export function useStudyLines(
  port: ChartPort | null,
  chart: ChartState,
  runtime: TerminalRuntime,
  atMs: number | null
): void {
  const plugins = useStore(indicatorPlugins(runtime));
  const layers = chart.indicator_layers;
  const studies = useMemo(
    () =>
      visibleStudies(
        plugins,
        layers,
        info => panePlots(info, 'price').length > 0
      ),
    [plugins, layers]
  );
  const results = useStudySeries(chart, runtime, atMs, studies);
  const lines = useRef(new Map<string, readonly PlotLine[]>());
  // The port is recreated on theme changes, so the colors follow it.
  const theme = useMemo<ChartTheme | null>(
    () => (port ? themeFromCss(cssVar) : null),
    [port]
  );
  useEffect(() => {
    if (!port) return;
    const disposers = studies.map(info =>
      port.addCanvasLayer(
        createPlotLayer({
          id: `study-lines:${info.plugin_id}`,
          // Above session separators (5), below drawings (100).
          zIndex: 10 + Math.round(info.display.z_index / 10),
          lines: () => lines.current.get(info.plugin_id) ?? [],
        })
      )
    );
    return () => {
      for (const dispose of disposers) {
        dispose();
      }
    };
  }, [port, studies]);
  const instrumentId = chart.instrument_id;
  useEffect(() => {
    if (!port || !theme) return;
    const next = new Map<string, readonly PlotLine[]>();
    for (const info of studies) {
      const series = results.get(info.plugin_id)?.series;
      if (!series || series.instrument_id !== instrumentId) continue;
      const style: IndicatorLayerStyle =
        layerOf(layers, info.plugin_id)?.style ?? {};
      next.set(
        info.plugin_id,
        plotLinesFromSeries(info, series, {
          theme,
          atMs,
          ...(style.colors ? {colors: style.colors} : {}),
        })
      );
    }
    lines.current = next;
    port.invalidate();
  }, [port, theme, studies, results, layers, atMs, instrumentId]);
}

/**
 * Shows a study pane below the chart for every pane a visible plugin
 * declares (except the volume delta pane, which `useDeltaPane` draws) and
 * the chart has not hidden. A failed request shows an empty pane that says
 * why, never invented values.
 * @param port Chart port (null before mount).
 * @param chart Chart state.
 * @param runtime Runtime whose plugin catalogue and market client are used.
 * @param atMs Replay point-in-time (UTC ms), or null when live.
 */
export function useStudyPanes(
  port: ChartPort | null,
  chart: ChartState,
  runtime: TerminalRuntime,
  atMs: number | null
): void {
  const plugins = useStore(indicatorPlugins(runtime));
  const layers = chart.indicator_layers;
  const instrumentId = chart.instrument_id;
  const studies = useMemo(
    () =>
      visibleStudies(
        plugins,
        layers,
        info =>
          info.plugin_id !== DELTA_PLUGIN_ID &&
          info.display.panes.some(pane =>
            paneShown(layerOf(layers, info.plugin_id)?.style ?? {}, pane.id)
          )
      ),
    [plugins, layers]
  );
  const results = useStudySeries(chart, runtime, atMs, studies);
  const specs = useMemo(() => {
    const list: StudyPaneSpec[] = [];
    for (const info of studies) {
      const style = layerOf(layers, info.plugin_id)?.style ?? {};
      const state = results.get(info.plugin_id);
      for (const pane of info.display.panes) {
        if (!paneShown(style, pane.id)) continue;
        if (state?.series && state.series.instrument_id === instrumentId) {
          list.push(studyPaneFromSeries(info, pane, state.series, atMs));
        } else if (state?.error) {
          list.push({
            id: studyPaneId(info.plugin_id, pane.id),
            title: pane.title,
            style: 'line',
            points: [],
            caption: `unavailable: ${state.error}`,
          });
        }
      }
    }
    return list;
  }, [studies, layers, results, atMs, instrumentId]);
  const shown = useRef(new Set<string>());
  useEffect(() => {
    if (!port) return;
    const next = new Set(specs.map(spec => spec.id));
    for (const id of shown.current) {
      if (!next.has(id)) port.removeStudyPane(id);
    }
    for (const spec of specs) {
      port.setStudyPane(spec);
    }
    shown.current = next;
  }, [port, specs]);
  useEffect(
    () => () => {
      for (const id of shown.current) {
        port?.removeStudyPane(id);
      }
      shown.current = new Set();
    },
    [port]
  );
}
