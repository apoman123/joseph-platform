/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Indicator layers feature: fetches backend AOIs (areas of
 * interest) and events per chart and publishes one overlay layer per
 * indicator plugin, styled by the plugin's display metadata. The layers
 * and their toggle commands are registered once the engine's plugin
 * catalogue arrives. Nothing is computed in the browser.
 */

import type {OverlayLayer} from '@joseph/chart-core';
import type {
  AoiList,
  ChartState,
  IndicatorPluginInfo,
  SeriesQuery,
} from '@joseph/contracts';
import {
  createStore,
  type Disposer,
  type ReadableStore,
} from '@joseph/feature-registry';
import {
  DEFAULT_VIZ_SETTINGS,
  buildIndicatorLayers,
  drawsRecords,
  layerPlugins,
  pluginMap,
  type VizSettings,
} from '@joseph/indicator-viz';
import {layerSettings, layerStyle, layerVisible} from '@joseph/terminal-shell';
import type {TerminalRuntime} from '../../composition/runtime';
import {indicatorPlugins} from '../../shell/indicator-plugins';
import type {WebFeature} from '../../shell/react-bindings';
import {createLazyStore} from '../shell/lazy-store';
import {shellManifest} from '../shell/manifest';

/** AOI poll interval in milliseconds; stream updates reload in between. */
const REFRESH_MS = 30_000;

/**
 * Merges a chart's per-layer kind filters, colors and event toggles into
 * the user's visualization settings.
 * @param base Feature-level settings.
 * @param chart Chart state.
 * @param plugins Plugin catalogue by id; a layer without an event setting
 *     takes the plugin's `display.show_events`.
 * @return Settings for this chart.
 */
export function chartVizSettings(
  base: VizSettings,
  chart: ChartState,
  plugins: ReadonlyMap<string, IndicatorPluginInfo> = new Map()
): VizSettings {
  const result = {...base.plugins};
  const ids = new Set([
    ...plugins.keys(),
    ...chart.indicator_layers.map(layer => layer.plugin_id),
  ]);
  for (const id of ids) {
    const layer = chart.indicator_layers.find(l => l.plugin_id === id);
    const style = layerStyle(chart, id);
    const current = result[id] ?? {
      visible: true,
      hiddenKinds: [],
      showEvents: true,
    };
    const chartEvents =
      style.show_events ?? plugins.get(id)?.display.show_events ?? true;
    result[id] = {
      ...current,
      showEvents: current.showEvents && chartEvents,
      ...(layer?.kinds ? {shownKinds: layer.kinds} : {}),
      ...(style.colors ? {colors: style.colors} : {}),
    };
  }
  return {...base, plugins: result};
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
 * Creates the indicator layers feature: one chart layer per indicator
 * plugin that publishes AOIs or events, fed by backend records, and
 * commands that toggle layers and ended zones.
 * @param runtime Runtime.
 * @return Feature.
 */
export function indicatorsFeature(runtime: TerminalRuntime): WebFeature {
  return {
    manifest: shellManifest(
      'indicators',
      'Indicators',
      'Renders backend-computed indicator zones, levels and events. Never computes signals in the browser.',
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
       * Returns the shared AOI source of a query (series, point in time and
       * input overrides). Live sources poll and follow the stream; replay
       * sources load once and are not cached, since each cursor step is a
       * new query.
       * @param query Series query.
       * @return Lazy store of the latest AOI list; null before the first
       *     answer or after a failed request.
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
            // `snapshot_required` means the stream missed updates, so the
            // list is fetched again in full.
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
      /**
       * Registers the overlay layer of one plugin.
       * @param info Plugin descriptor.
       * @param plugins Whole catalogue by id (styles and names).
       * @return Disposer removing the layer from every chart.
       */
      const registerLayer = (
        info: IndicatorPluginInfo,
        plugins: ReadonlyMap<string, IndicatorPluginInfo>
      ): Disposer =>
        ctx.chartLayers.register(chart =>
          createLazyStore<OverlayLayer | null>(null, set => {
            const pluginId = info.plugin_id;
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
              // Every plugin's layer is built; this provider keeps its own.
              const layer = buildIndicatorLayers({
                instrumentId: chart.instrumentId,
                timeframe: chart.timeframe,
                aois: value.aois,
                events: value.events ?? [],
                asOfMs: value.as_of_ms,
                feedState: value.feed_state,
                settings: chartVizSettings(settings.get(), state, plugins),
                plugins,
                atMs: chart.atMs ?? null,
              }).find(l => l.layerId === `indicator-viz:${pluginId}`);
              // A layer the chart does not list follows the plugin default.
              const visible = layerVisible(
                state,
                pluginId,
                info.display.default_visible
              );
              set(layer ? {...layer, visible} : null);
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
      /**
       * Registers the toggle command of one Layers menu plugin.
       * @param info Plugin descriptor.
       * @return Disposer removing the command.
       */
      const registerToggle = (info: IndicatorPluginInfo): Disposer =>
        ctx.commands.register({
          id: `indicators.toggle.${info.plugin_id}`,
          title: `Show or hide ${info.display_name} on the active chart`,
          run: () => {
            const chartId = runtime.selection.get().chartId;
            const chart = runtime.workspace
              .get()
              .charts.find(c => c.chart_id === chartId);
            const visible = chart
              ? layerVisible(
                  chart,
                  info.plugin_id,
                  info.display.default_visible
                )
              : info.display.default_visible;
            runtime.dispatch({
              type: 'setIndicatorLayer',
              chartId,
              pluginId: info.plugin_id,
              visible: !visible,
            });
          },
        });
      const catalogue = indicatorPlugins(runtime);
      let registered: Disposer[] = [];
      let registeredFor: readonly IndicatorPluginInfo[] | null = null;
      /**
       * Replaces the per-plugin layers and commands when the catalogue
       * changes.
       * @param infos Plugin descriptors; null before the engine answers.
       */
      const register = (infos: readonly IndicatorPluginInfo[] | null) => {
        if (infos === registeredFor) return;
        registeredFor = infos;
        for (const dispose of registered.splice(0)) {
          dispose();
        }
        if (!infos) return;
        const plugins = pluginMap(infos);
        registered = [
          ...infos.filter(drawsRecords).map(i => registerLayer(i, plugins)),
          ...layerPlugins(infos).map(registerToggle),
        ];
      };
      const offCatalogue = catalogue.subscribe(register);
      register(catalogue.get());
      ctx.commands.register({
        id: 'indicators.toggle-ended',
        title: 'Show or hide ended zones (invalidated, expired)',
        run: () => void settings.update(s => ({...s, showEnded: !s.showEnded})),
      });
      return () => {
        offCatalogue();
        for (const dispose of registered.splice(0)) {
          dispose();
        }
        cache.clear();
      };
    },
  };
}
