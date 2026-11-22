/**
 * @fileoverview Indicator layers feature: fetches backend AOIs/events per
 * chart and publishes one overlay layer per indicator plugin.
 */
import type {OverlayLayer} from '@joseph/chart-core';
import type {AoiList} from '@joseph/contracts';
import type {ReadableStore} from '@joseph/feature-registry';
import {
  DEFAULT_VIZ_SETTINGS,
  PLUGIN_NAMES,
  buildIndicatorLayers,
  type VizSettings,
} from '@joseph/indicator-viz';
import {createStore} from '@joseph/feature-registry';
import type {TerminalRuntime} from '../../composition/runtime';
import type {WebFeature} from '../../shell/react-bindings';
import {createLazyStore} from '../shell/lazy-store';
import {shellManifest} from '../shell/manifest';

/** Poll interval for AOI refresh. */
const REFRESH_MS = 30_000;

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
      settings.subscribe(value => ctx.settings.write(value));
      const cache = new Map<string, ReadableStore<AoiList | null>>();
      const source = (
        instrumentId: string,
        timeframe: AoiList['timeframe']
      ) => {
        const key = `${instrumentId}|${timeframe}`;
        let store = cache.get(key);
        if (!store) {
          store = createLazyStore<AoiList | null>(null, set => {
            let alive = true;
            const load = () => {
              ctx.clients.market
                .getAois({instrumentId, timeframe})
                .then(list => {
                  if (alive) set(list);
                })
                .catch(() => {
                  if (alive) set(null);
                });
            };
            load();
            const timer = setInterval(load, REFRESH_MS);
            const unsubscribe = ctx.clients.marketStream.subscribe(
              [{channel: 'aois', instrument_id: instrumentId, timeframe}],
              envelope => {
                if (
                  envelope.type === 'aoi.updated' ||
                  envelope.type === 'snapshot_required'
                )
                  load();
              }
            );
            return () => {
              alive = false;
              clearInterval(timer);
              unsubscribe();
            };
          });
          cache.set(key, store);
        }
        return store;
      };
      for (const pluginId of Object.keys(PLUGIN_NAMES)) {
        ctx.chartLayers.register(chart =>
          createLazyStore<OverlayLayer | null>(null, set => {
            const list = source(chart.instrumentId, chart.timeframe);
            const render = () => {
              const value = list.get();
              if (!value) {
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
                settings: settings.get(),
              }).find(l => l.layerId === `indicator-viz:${pluginId}`);
              set(layer ?? null);
            };
            const offList = list.subscribe(render);
            const offSettings = settings.subscribe(render);
            render();
            return () => {
              offList();
              offSettings();
            };
          })
        );
      }
      ctx.commands.register({
        id: 'indicators.toggle-ended',
        title: 'Show or hide ended zones (invalidated, swept, expired)',
        run: () => settings.update(s => ({...s, showEnded: !s.showEnded})),
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
      return () => cache.clear();
    },
  };
}
