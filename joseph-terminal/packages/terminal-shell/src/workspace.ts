/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Workspace document model and pure reducer.
 *
 * Chart tabs have no fixed count limit; the layout shows up to eight panes,
 * so shrinking a layout never discards a chart's settings. Instrument,
 * timeframe and viewport changes propagate to linked charts when the
 * corresponding sync option is on (charts with a link group sync only
 * within that group).
 */

import type {
  ChartState,
  ChartType,
  FeedMode,
  InstrumentId,
  LayoutId,
  PriceScaleMode,
  SyncOptions,
  Timeframe,
  WorkspaceDocument,
} from '@joseph/contracts';

/** Number of chart panes each layout shows. */
export const LAYOUT_PANES: Readonly<Record<LayoutId, number>> = {
  '1': 1,
  '2h': 2,
  '2v': 2,
  '4': 4,
  '8': 8,
};

/** Maximum chart panes visible at once; saved tabs have no count limit. */
export const MAX_VISIBLE_CHARTS = 8;

/**
 * Indicator layers of a new chart, with their initial visibility: the
 * built-in EMA, volume delta and pivot points shown, the other built-in
 * layers listed but hidden. Plugins the engine does not serve are ignored,
 * and installed plugins missing here follow their own display default.
 */
export const DEFAULT_INDICATOR_LAYERS: ChartState['indicator_layers'] = [
  {plugin_id: 'ema', visible: true},
  {plugin_id: 'volume_delta', visible: true},
  {plugin_id: 'pivot_points', visible: true},
  {plugin_id: 'anchored_vwap', visible: false},
  {plugin_id: 'sma', visible: false},
  {plugin_id: 'bollinger_bands', visible: false},
  {plugin_id: 'rsi', visible: false},
  {plugin_id: 'atr', visible: false},
];

/** Maximum instruments compared on one chart. */
export const MAX_COMPARE = 3;

/** Presentation settings stored in `IndicatorLayerState.style`. */
export interface IndicatorLayerStyle {
  /**
   * User colors per area-of-interest (AOI) kind, event name or plotted
   * value key (`#rrggbb`).
   */
  colors?: Readonly<Record<string, string>>;
  /**
   * Whether event markers are drawn; the plugin's `display.show_events`
   * when unset.
   */
  show_events?: boolean;
  /** Whether the plugin's sub-panes are shown (default true). */
  pane?: boolean;
  /** Visibility per sub-pane id; a pane missing here follows `pane`. */
  panes?: Readonly<Record<string, boolean>>;
}

/** Indicator layer configuration patch (visibility is set separately). */
export interface IndicatorLayerPatch {
  /** Kinds shown; null shows every kind. */
  kinds?: ChartState['indicator_layers'][number]['kinds'] | null;
  /** Presentation settings (replaces the previous style). */
  style?: IndicatorLayerStyle;
  /** Indicator inputs; null restores the plugin defaults. */
  settings?: Readonly<Record<string, unknown>> | null;
  /**
   * Visibility of a layer the chart does not list yet (default true); a
   * listed layer keeps its own.
   */
  visible?: boolean;
}

/**
 * Returns a layer's presentation settings.
 * @param chart Chart holding the layer.
 * @param pluginId Indicator plugin id.
 * @return Style; empty when the layer or its style is missing.
 */
export function layerStyle(
  chart: ChartState,
  pluginId: string
): IndicatorLayerStyle {
  const style = chart.indicator_layers.find(
    l => l.plugin_id === pluginId
  )?.style;
  return style ?? {};
}

/**
 * Returns whether one of a layer's sub-panes is shown (the layer itself
 * may still be hidden; see `layerVisible`).
 * @param style Layer style.
 * @param paneId Pane id from the plugin's `display.panes`.
 * @return The pane's own setting, else the layer-wide `pane` setting,
 *     else true.
 */
export function paneShown(style: IndicatorLayerStyle, paneId: string): boolean {
  return style.panes?.[paneId] ?? style.pane !== false;
}

/**
 * Returns a layer's indicator inputs.
 * @param chart Chart holding the layer.
 * @param pluginId Indicator plugin id.
 * @return Inputs, or null when the layer has none (missing or empty), so the
 *     plugin defaults apply.
 */
export function layerSettings(
  chart: ChartState,
  pluginId: string
): Readonly<Record<string, unknown>> | null {
  // The contract types `settings` as an open object (`{}`); its keys are
  // the plugin's input names.
  const settings = chart.indicator_layers.find(l => l.plugin_id === pluginId)
    ?.settings as Record<string, unknown> | undefined;
  return settings && Object.keys(settings).length > 0 ? settings : null;
}

/**
 * Returns whether a plugin layer is shown.
 * @param chart Chart holding the layer.
 * @param pluginId Indicator plugin id.
 * @param fallback Visibility of a layer the chart does not list, usually
 *     the plugin's `display.default_visible` (default true).
 * @return The layer's own visibility, else `fallback`.
 */
export function layerVisible(
  chart: ChartState,
  pluginId: string,
  fallback = true
): boolean {
  return (
    chart.indicator_layers.find(l => l.plugin_id === pluginId)?.visible ??
    fallback
  );
}

/** Inputs for a fresh workspace. */
export interface DefaultWorkspaceInput {
  /** Workspace id, also the storage key suffix. */
  workspaceId: string;
  /** Instrument for the first chart. */
  instrumentId: InstrumentId;
  /** Timeframe for the first chart. */
  timeframe: Timeframe;
  /** Creation time (UTC epoch ms), stored as `updated_at_ms`. */
  nowMs: number;
}

/**
 * Creates a chart with the default presentation: candles, linear scale,
 * volume, `DEFAULT_INDICATOR_LAYERS`, no link group, `AUTO` feed and no
 * saved viewport.
 * @param chartId Chart id, unique within the workspace (`chart-<n>`).
 * @param instrumentId Instrument shown.
 * @param timeframe Bar timeframe.
 * @return Chart state.
 */
export function createChartState(
  chartId: string,
  instrumentId: InstrumentId,
  timeframe: Timeframe
): ChartState {
  return {
    chart_id: chartId,
    instrument_id: instrumentId,
    timeframe,
    chart_type: 'candles',
    price_scale: 'linear',
    show_volume: true,
    indicator_layers: DEFAULT_INDICATOR_LAYERS.map(layer => ({...layer})),
    link_group: null,
    feed_mode: 'AUTO',
    visible_range: null,
  };
}

/**
 * Creates the default single-chart workspace: layout `1`, crosshair sync
 * only, and the default favorite timeframes.
 * @param input Workspace id, first chart and clock.
 * @return Workspace document (schema v1).
 */
export function createDefaultWorkspace(
  input: DefaultWorkspaceInput
): WorkspaceDocument {
  return {
    schema_version: 1,
    workspace_id: input.workspaceId,
    name: 'My workspace',
    layout: '1',
    sync: {
      crosshair: true,
      instrument: false,
      timeframe: false,
      viewport: false,
    },
    active_chart_id: 'chart-1',
    charts: [createChartState('chart-1', input.instrumentId, input.timeframe)],
    favorites: {timeframes: ['5m', '15m', '1h', '4h', '1d'], instruments: []},
    panels: {},
    updated_at_ms: input.nowMs,
  };
}

/**
 * Workspace actions. Pop-out windows send these over the window channel,
 * so they stay structured-cloneable data.
 */
export type WorkspaceAction =
  | {type: 'setLayout'; layout: LayoutId}
  | {type: 'setActiveChart'; chartId: string}
  | {
      /** Creates and selects an independent saved chart tab. */
      type: 'addChart';
      /** Unique chart identifier within this workspace. */
      chartId: string;
      /** Optional canonical instrument; defaults to the active chart's symbol. */
      instrumentId?: InstrumentId;
      /** Tab content; existing callers create candlestick charts. */
      viewType?: 'candles' | 'heatmap';
    }
  | {
      /** Removes a chart tab while preserving at least one chart. */
      type: 'closeChart';
      /** Identifier of the saved chart to remove. */
      chartId: string;
    }
  | {type: 'setInstrument'; chartId: string; instrumentId: InstrumentId}
  | {type: 'setTimeframe'; chartId: string; timeframe: Timeframe}
  | {type: 'setChartType'; chartId: string; chartType: ChartType}
  | {type: 'setPriceScale'; chartId: string; priceScale: PriceScaleMode}
  | {type: 'setFeedMode'; chartId: string; feedMode: FeedMode}
  | {type: 'toggleVolume'; chartId: string}
  | {
      type: 'setIndicatorLayer';
      chartId: string;
      pluginId: string;
      visible: boolean;
    }
  | {
      /** Removes an indicator and clears its settings, unlike hiding it. */
      type: 'removeIndicatorLayer';
      chartId: string;
      pluginId: string;
    }
  | {
      type: 'setIndicatorLayerConfig';
      chartId: string;
      pluginId: string;
      patch: IndicatorLayerPatch;
    }
  | {
      type: 'setCompare';
      chartId: string;
      instrumentIds: readonly InstrumentId[];
    }
  | {type: 'toggleSessionSeparators'; chartId: string}
  | {
      type: 'applyChartPresentation';
      chartId: string;
      presentation: ChartPresentation;
    }
  | {type: 'setLinkGroup'; chartId: string; group: ChartState['link_group']}
  | {type: 'setSync'; key: keyof SyncOptions; value: boolean}
  | {
      type: 'setVisibleRange';
      chartId: string;
      range: ChartState['visible_range'];
    }
  | {type: 'toggleFavoriteTimeframe'; timeframe: Timeframe}
  | {type: 'toggleFavoriteInstrument'; instrumentId: InstrumentId}
  | {type: 'rename'; name: string}
  | {type: 'setPanel'; featureId: string; state: unknown}
  | {type: 'replace'; workspace: WorkspaceDocument};

/** Chart presentation captured by templates (never instrument-specific). */
export type ChartPresentation = Pick<
  ChartState,
  'chart_type' | 'price_scale' | 'show_volume' | 'indicator_layers'
> & {session_separators?: boolean};

/**
 * Applies a configuration patch to one indicator layer. Fields the patch
 * omits are kept; `null` removes `kinds` or `settings`.
 * @param layers Current layers.
 * @param pluginId Indicator plugin id.
 * @param patch Fields to change.
 * @return New layer list; a missing layer is appended first, visible as
 *     `patch.visible` says (default true).
 */
export function patchIndicatorLayer(
  layers: ChartState['indicator_layers'],
  pluginId: string,
  patch: IndicatorLayerPatch
): ChartState['indicator_layers'] {
  const exists = layers.some(l => l.plugin_id === pluginId);
  const base = exists
    ? layers
    : [...layers, {plugin_id: pluginId, visible: patch.visible ?? true}];
  return base.map(layer => {
    if (layer.plugin_id !== pluginId) return layer;
    const next = {...layer};
    if (patch.kinds === null) delete next.kinds;
    else if (patch.kinds !== undefined) next.kinds = [...patch.kinds];
    if (patch.style !== undefined) next.style = {...patch.style};
    if (patch.settings === null) delete next.settings;
    else if (patch.settings !== undefined) next.settings = {...patch.settings};
    return next;
  });
}

/**
 * Returns the charts that follow a change made on `chartId`. Only charts
 * visible in the current layout follow; when the source chart has a link
 * group, only charts of that group do.
 * @param doc Workspace.
 * @param chartId Chart the change was made on.
 * @param synced Whether the sync option for this kind of change is on.
 * @return Chart ids to update; always includes `chartId`.
 */
export function linkedChartIds(
  doc: WorkspaceDocument,
  chartId: string,
  synced: boolean
): Set<string> {
  const ids = new Set([chartId]);
  if (!synced) return ids;
  const source = doc.charts.find(chart => chart.chart_id === chartId);
  const visible = visibleCharts(doc);
  for (const chart of visible) {
    const group = source?.link_group ?? null;
    if (group === null || chart.link_group === group) ids.add(chart.chart_id);
  }
  return ids;
}

/**
 * Applies a change to some charts of a workspace.
 * @param doc Workspace.
 * @param ids Ids of the charts to change.
 * @param change Returns the new state of one chart.
 * @return Chart list with the changed charts, in the original order.
 */
function mapCharts(
  doc: WorkspaceDocument,
  ids: Set<string>,
  change: (chart: ChartState) => ChartState
): WorkspaceCharts {
  return asCharts(
    doc.charts.map(chart => (ids.has(chart.chart_id) ? change(chart) : chart))
  );
}

/** Nonempty chart list type of the generated contract. */
export type WorkspaceCharts = WorkspaceDocument['charts'];

/**
 * Narrows a chart array to the contract's nonempty list type. The length is
 * not checked.
 * @param charts Charts; the caller guarantees at least one item.
 * @return The same array, typed as the contract's chart list.
 */
export function asCharts(charts: ChartState[]): WorkspaceCharts {
  return charts as WorkspaceCharts;
}

/**
 * Adds a value to a list, or removes it when present.
 * @param list Current list; undefined counts as empty.
 * @param value Value to toggle.
 * @return New list; an added value goes last.
 */
function toggle<T>(list: readonly T[] | undefined, value: T): T[] {
  const items = [...(list ?? [])];
  return items.includes(value)
    ? items.filter(item => item !== value)
    : [...items, value];
}

/**
 * Applies a workspace action (pure) and stamps `updated_at_ms` when anything
 * changed.
 * @param doc Current workspace.
 * @param action Action to apply.
 * @param nowMs Current time (UTC epoch ms) for `updated_at_ms`.
 * @return Next workspace; `doc` itself when the action changed nothing.
 */
export function workspaceReducer(
  doc: WorkspaceDocument,
  action: WorkspaceAction,
  nowMs: number
): WorkspaceDocument {
  const next = reduce(doc, action);
  return next === doc ? doc : {...next, updated_at_ms: nowMs};
}

/**
 * Applies a workspace action without touching `updated_at_ms`.
 * @param doc Current workspace.
 * @param action Action to apply.
 * @return Next workspace; `doc` itself for no-op actions.
 */
function reduce(
  doc: WorkspaceDocument,
  action: WorkspaceAction
): WorkspaceDocument {
  switch (action.type) {
    case 'replace':
      return action.workspace;
    case 'addChart': {
      if (doc.charts.some(chart => chart.chart_id === action.chartId)) {
        return doc;
      }
      const source = activeChart(doc);
      const chart = createChartState(
        action.chartId,
        action.instrumentId ?? source.instrument_id,
        source.timeframe
      );
      if (action.viewType) chart.view_type = action.viewType;
      return {
        ...doc,
        charts: asCharts([...doc.charts, chart]),
        active_chart_id: chart.chart_id,
      };
    }
    case 'closeChart': {
      if (doc.charts.length === 1) return doc;
      const index = doc.charts.findIndex(
        chart => chart.chart_id === action.chartId
      );
      if (index < 0) return doc;
      const charts = doc.charts.filter(
        chart => chart.chart_id !== action.chartId
      );
      const fallback = charts[Math.min(index, charts.length - 1)];
      return {
        ...doc,
        charts: asCharts(charts),
        active_chart_id:
          doc.active_chart_id === action.chartId
            ? (fallback?.chart_id ?? doc.active_chart_id)
            : doc.active_chart_id,
      };
    }
    case 'setLayout': {
      const count = LAYOUT_PANES[action.layout];
      const activeIndex = Math.max(
        0,
        doc.charts.findIndex(chart => chart.chart_id === doc.active_chart_id)
      );
      const first = Math.floor(activeIndex / count) * count;
      const needed = first + count;
      const charts = [...doc.charts];
      const template =
        charts.find(chart => chart.chart_id === doc.active_chart_id) ??
        charts[0];
      // New panes copy the active chart (without its viewport). Charts beyond
      // the layout are kept, so shrinking and growing again restores them.
      let seq = charts.length;
      while (charts.length < needed && template) {
        seq += 1;
        let id = `chart-${seq}`;
        while (charts.some(chart => chart.chart_id === id)) {
          seq += 1;
          id = `chart-${seq}`;
        }
        charts.push({
          ...structuredClone(template),
          chart_id: id,
          visible_range: null,
        });
      }
      const visibleIds = charts
        .slice(first, needed)
        .map(chart => chart.chart_id);
      const active = visibleIds.includes(doc.active_chart_id)
        ? doc.active_chart_id
        : (visibleIds[0] ?? doc.active_chart_id);
      return {
        ...doc,
        layout: action.layout,
        charts: asCharts(charts),
        active_chart_id: active,
      };
    }
    case 'setActiveChart':
      return doc.charts.some(chart => chart.chart_id === action.chartId)
        ? {...doc, active_chart_id: action.chartId}
        : doc;
    case 'setInstrument':
      return {
        ...doc,
        charts: mapCharts(
          doc,
          linkedChartIds(doc, action.chartId, doc.sync.instrument),
          chart => ({
            ...chart,
            instrument_id: action.instrumentId,
            visible_range: null,
          })
        ),
      };
    case 'setTimeframe':
      return {
        ...doc,
        charts: mapCharts(
          doc,
          linkedChartIds(doc, action.chartId, doc.sync.timeframe),
          chart => ({
            ...chart,
            timeframe: action.timeframe,
            visible_range: null,
          })
        ),
      };
    case 'setVisibleRange':
      return {
        ...doc,
        charts: mapCharts(
          doc,
          linkedChartIds(doc, action.chartId, doc.sync.viewport),
          chart => ({...chart, visible_range: action.range})
        ),
      };
    case 'setChartType':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => ({
          ...chart,
          chart_type: action.chartType,
        })),
      };
    case 'setPriceScale':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => ({
          ...chart,
          price_scale: action.priceScale,
        })),
      };
    case 'setFeedMode':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => ({
          ...chart,
          feed_mode: action.feedMode,
        })),
      };
    case 'toggleVolume':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => ({
          ...chart,
          show_volume: !chart.show_volume,
        })),
      };
    case 'setIndicatorLayer':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => {
          const exists = chart.indicator_layers.some(
            l => l.plugin_id === action.pluginId
          );
          const layers = exists
            ? chart.indicator_layers.map(layer =>
                layer.plugin_id === action.pluginId
                  ? layer.removed && action.visible
                    ? {plugin_id: layer.plugin_id, visible: true, present: true}
                    : {...layer, visible: action.visible, present: true}
                  : layer
              )
            : [
                ...chart.indicator_layers,
                {
                  plugin_id: action.pluginId,
                  visible: action.visible,
                  present: true,
                },
              ];
          return {...chart, indicator_layers: layers};
        }),
      };
    case 'removeIndicatorLayer':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => ({
          ...chart,
          indicator_layers: [
            ...chart.indicator_layers.filter(
              layer => layer.plugin_id !== action.pluginId
            ),
            {plugin_id: action.pluginId, visible: false, removed: true},
          ],
        })),
      };
    case 'setIndicatorLayerConfig':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => ({
          ...chart,
          indicator_layers: patchIndicatorLayer(
            chart.indicator_layers,
            action.pluginId,
            action.patch
          ),
        })),
      };
    case 'setCompare': {
      const ids = [...new Set(action.instrumentIds)].slice(0, MAX_COMPARE);
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => {
          const {compare: _previous, ...rest} = chart;
          const kept = ids.filter(id => id !== chart.instrument_id);
          // `ids` is capped at MAX_COMPARE, so `kept` fits the contract's
          // tuple of at most three instruments.
          return kept.length > 0
            ? {...rest, compare: kept as ChartState['compare']}
            : rest;
        }),
      };
    }
    case 'toggleSessionSeparators':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => ({
          ...chart,
          session_separators: !chart.session_separators,
        })),
      };
    case 'applyChartPresentation':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => ({
          ...chart,
          chart_type: action.presentation.chart_type,
          price_scale: action.presentation.price_scale,
          show_volume: action.presentation.show_volume,
          indicator_layers: structuredClone(
            action.presentation.indicator_layers
          ),
          session_separators: action.presentation.session_separators === true,
        })),
      };
    case 'setLinkGroup':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => ({
          ...chart,
          link_group: action.group,
        })),
      };
    case 'setSync':
      return doc.sync[action.key] === action.value
        ? doc
        : {...doc, sync: {...doc.sync, [action.key]: action.value}};
    case 'toggleFavoriteTimeframe':
      return {
        ...doc,
        favorites: {
          ...doc.favorites,
          timeframes: toggle(doc.favorites?.timeframes, action.timeframe),
        },
      };
    case 'toggleFavoriteInstrument':
      return {
        ...doc,
        favorites: {
          ...doc.favorites,
          instruments: toggle(doc.favorites?.instruments, action.instrumentId),
        },
      };
    case 'rename': {
      const name = action.name.trim().slice(0, 120);
      return name ? {...doc, name} : doc;
    }
    case 'setPanel':
      return {
        ...doc,
        panels: {...(doc.panels ?? {}), [action.featureId]: action.state},
      };
    default:
      return doc;
  }
}

/**
 * Returns the page of chart tabs containing the active chart.
 * Single-chart layouts mount only the selected tab; split layouts mount
 * at most their configured pane count.
 * @param doc Workspace.
 * @return Visible charts in pane order.
 */
export function visibleCharts(doc: WorkspaceDocument): ChartState[] {
  const count = LAYOUT_PANES[doc.layout];
  const index = Math.max(
    0,
    doc.charts.findIndex(chart => chart.chart_id === doc.active_chart_id)
  );
  const start = Math.floor(index / count) * count;
  return doc.charts.slice(start, start + count);
}

/**
 * Returns the active chart state.
 * @param doc Workspace.
 * @return Active chart, or the first chart when `active_chart_id` is stale.
 * @throws `Error` when the workspace has no charts, which a valid document
 *     never has (the contract requires at least one).
 */
export function activeChart(doc: WorkspaceDocument): ChartState {
  const chart =
    doc.charts.find(c => c.chart_id === doc.active_chart_id) ?? doc.charts[0];
  if (!chart) throw new Error('Workspace has no charts');
  return chart;
}
