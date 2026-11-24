/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Indicator layers feature: fetches backend AOIs/events per
 * chart and publishes one overlay layer per indicator plugin.
 */

import type {OverlayLayer} from '@joseph/chart-core';
import type {AoiList, ChartState, SeriesQuery} from '@joseph/contracts';
import {createStore, type ReadableStore} from '@joseph/feature-registry';
import {
  DEFAULT_VIZ_SETTINGS,
  PLUGIN_NAMES,
  buildIndicatorLayers,
  type VizSettings,
} from '@joseph/indicator-viz';
import {layerSettings, layerStyle} from '@joseph/terminal-shell';
import type {TerminalRuntime} from '../../composition/runtime';
import type {WebFeature} from '../../shell/react-bindings';
import {createLazyStore} from '../shell/lazy-store';
import {shellManifest} from '../shell/manifest';

/** Poll interval for AOI refresh. */
const REFRESH_MS = 30_000;

/**
 * Merges a chart's per-layer kind filters, colors and event toggles into
 * the user's visualization settings.
 * @param base Feature-level settings.
 * @param chart Chart state.
 * @return Settings for this chart.
 */
export function chartVizSettings(
  base: VizSettings,
  chart: ChartState
): VizSettings {
  const plugins = {...base.plugins};
  for (const layer of chart.indicator_layers) {
    const style = layerStyle(chart, layer.plugin_id);
    const current = plugins[layer.plugin_id] ?? {
      visible: true,
      hiddenKinds: [],
      showEvents: true,
    };
    plugins[layer.plugin_id] = {
      ...current,
      showEvents: current.showEvents && style.show_events !== false,
      ...(layer.kinds ? {shownKinds: layer.kinds} : {}),
      ...(style.colors ? {colors: style.colors} : {}),
    };
  }
  return {...base, plugins};
}

/**
 * Collects a chart's indicator input overrides for backend queries.
 * @param chart Chart state.
 * @return `{settings}` when any plugin has overrides, else empty.
 */
export function chartQuerySettings(chart: ChartState): {
  settings?: Record<string, Readonly<Record<string, unknown>>>;
} {
  const settings: Record<string, Readonly<Record<string, unknown>>> = {};
  for (const layer of chart.indicator_layers) {
    const values = layerSettings(chart, layer.plugin_id);
    if (values) settings[layer.plugin_id] = values;
  }
  return Object.keys(settings).length > 0 ? {settings} : {};
}

/**
 * Indicator visualization feature.
 * @param runtime Runtime.
 * @return Feature.
 */
export function indicatorsFeature(runtime: TerminalRuntime): WebFeature {
  return {
    manifest: shellManifest(
      'indicators',
      'Indicator layers',
      'Renders backend-computed SMC zones and events. Never computes signals in the browser.',
      {
        uiSlots: ['chart-overlay'],
        requiredCapabilities: ['market'],
        permissions: ['market:read'],
      }
    ),
    activate(ctx) {
      const settings = createStore<VizSettings>(
        ctx.settings.read(DEFAULT_VIZ_SETTINGS)
      );
      settings.subscribe(value => void ctx.settings.write(value));
      const cache = new Map<string, ReadableStore<AoiList | null>>();
      /**
       * Shared AOI source per series, point-in-time and input overrides.
       * Replay sources are not cached (each cursor step is a new query).
       * @param query Series query.
       * @return Lazy store of the latest AOI list.
       */
      const source = (query: SeriesQuery): ReadableStore<AoiList | null> => {
        const key = JSON.stringify([
          query.instrumentId,
          query.timeframe,
          query.atMs ?? null,
          query.settings ?? null,
        ]);
        const cached = cache.get(key);
        if (cached) return cached;
        const store = createLazyStore<AoiList | null>(null, set => {
          let alive = true;
          let warned = false;
          const load = () => {
            ctx.clients.market
              .getAois(query)
              .then(list => {
                if (alive) set(list);
              })
              .catch((error: unknown) => {
                if (!alive) return;
                set(null);
                // Rejected inputs must not make zones vanish silently.
                if (query.settings && !warned) {
                  warned = true;
                  ctx.notifier.notify(
                    `The engine rejected indicator settings: ${
                      error instanceof Error ? error.message : 'request failed'
                    }. Open the layer settings to fix or reset them.`,
                    'error'
                  );
                }
              });
          };
          load();
          if (query.atMs !== undefined) {
            return () => {
              alive = false;
            };
          }
          const timer = setInterval(load, REFRESH_MS);
          const unsubscribe = ctx.clients.marketStream.subscribe(
            [
              {
                channel: 'aois',
                instrument_id: query.instrumentId,
                timeframe: query.timeframe,
              },
            ],
            envelope => {
              if (
                envelope.type === 'aoi.updated' ||
                envelope.type === 'snapshot_required'
              ) {
                load();
              }
            }
          );
          return () => {
            alive = false;
            clearInterval(timer);
            unsubscribe();
          };
        });
        if (query.atMs === undefined) cache.set(key, store);
        return store;
      };
      for (const pluginId of Object.keys(PLUGIN_NAMES)) {
        ctx.chartLayers.register(chart =>
          createLazyStore<OverlayLayer | null>(null, set => {
            const chartState = () =>
              runtime.workspace
                .get()
                .charts.find(c => c.chart_id === chart.chartId);
            let currentKey = '';
            let offList: (() => void) | null = null;
            let list: ReadableStore<AoiList | null> | null = null;
            const render = () => {
              const value = list?.get();
              const state = chartState();
              if (!value || !state) {
                set(null);
                return;
              }
              const layer = buildIndicatorLayers({
                instrumentId: chart.instrumentId,
                timeframe: chart.timeframe,
                aois: value.aois,
                events: value.events ?? [],
                asOfMs: value.as_of_ms,
                feedState: value.feed_state,
                settings: chartVizSettings(settings.get(), state),
                atMs: chart.atMs ?? null,
              }).find(l => l.layerId === `indicator-viz:${pluginId}`);
              set(layer ?? null);
            };
            let lastLayers: ChartState['indicator_layers'] | null = null;
            const attach = () => {
              const state = chartState();
              // Pans and other chart edits do not touch this layer.
              if (state && state.indicator_layers === lastLayers) return;
              lastLayers = state?.indicator_layers ?? null;
              const query: SeriesQuery = {
                instrumentId: chart.instrumentId,
                timeframe: chart.timeframe,
                ...(chart.atMs != null ? {atMs: chart.atMs} : {}),
                ...(state ? chartQuerySettings(state) : {}),
              };
              const key = JSON.stringify(query);
              if (key !== currentKey) {
                currentKey = key;
                offList?.();
                list = source(query);
                offList = list.subscribe(render);
              }
              render();
            };
            const offWorkspace = runtime.workspace.subscribe(attach);
            const offSettings = settings.subscribe(render);
            attach();
            return () => {
              offList?.();
              offWorkspace();
              offSettings();
            };
          })
        );
      }
      ctx.commands.register({
        id: 'indicators.toggle-ended',
        title: 'Show or hide ended zones (invalidated, swept, expired)',
        run: () => void settings.update(s => ({...s, showEnded: !s.showEnded})),
      });
      for (const [pluginId, name] of Object.entries(PLUGIN_NAMES)) {
        ctx.commands.register({
          id: `indicators.toggle.${pluginId}`,
          title: `Show or hide ${name.toLowerCase()} on the active chart`,
          run: () => {
            const chart = runtime.workspace
              .get()
              .charts.find(c => c.chart_id === runtime.selection.get().chartId);
            const visible =
              chart?.indicator_layers.find(l => l.plugin_id === pluginId)
                ?.visible ?? false;
            runtime.dispatch({
              type: 'setIndicatorLayer',
              chartId: runtime.selection.get().chartId,
              pluginId,
              visible: !visible,
            });
          },
        });
      }
      return () => void cache.clear();
    },
  };
}
