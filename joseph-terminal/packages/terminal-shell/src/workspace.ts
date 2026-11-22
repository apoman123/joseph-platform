/**
 * @fileoverview Workspace document model and pure reducer.
 *
 * Up to eight chart states are kept; the layout decides how many are shown,
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

/** Visible panes per layout. */
export const LAYOUT_PANES: Readonly<Record<LayoutId, number>> = {
  '1': 1,
  '2h': 2,
  '2v': 2,
  '4': 4,
  '8': 8,
};

/** Maximum charts stored in a workspace. */
export const MAX_CHARTS = 8;

/** Default indicator layers shown on new charts. */
export const DEFAULT_INDICATOR_LAYERS: ChartState['indicator_layers'] = [
  {plugin_id: 'fvg_ifvg', visible: true},
  {plugin_id: 'order_breaker_blocks', visible: true},
  {plugin_id: 'liquidity_sweeps', visible: true},
  {plugin_id: 'premium_discount_ote', visible: false},
  {plugin_id: 'auction_value_areas', visible: false},
  {plugin_id: 'daily_bias', visible: true},
  {plugin_id: 'order_flow_pressure', visible: true},
];

/** Inputs for a fresh workspace. */
export interface DefaultWorkspaceInput {
  /** Workspace id. */
  workspaceId: string;
  /** Instrument for the first chart. */
  instrumentId: InstrumentId;
  /** Timeframe for the first chart. */
  timeframe: Timeframe;
  /** Clock. */
  nowMs: number;
}

/**
 * Creates a chart state.
 * @param chartId Chart id.
 * @param instrumentId Instrument.
 * @param timeframe Timeframe.
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
 * Creates the default single-chart workspace.
 * @param input Inputs.
 * @return Workspace document.
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

/** Workspace actions. */
export type WorkspaceAction =
  | {type: 'setLayout'; layout: LayoutId}
  | {type: 'setActiveChart'; chartId: string}
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

/**
 * Charts that should follow a change made on `chartId`.
 * @param doc Workspace.
 * @param chartId Source chart.
 * @param synced Whether the relevant sync option is on.
 * @return Chart ids to update (always includes the source).
 */
export function linkedChartIds(
  doc: WorkspaceDocument,
  chartId: string,
  synced: boolean
): Set<string> {
  const ids = new Set([chartId]);
  if (!synced) return ids;
  const source = doc.charts.find(chart => chart.chart_id === chartId);
  const visible = doc.charts.slice(0, LAYOUT_PANES[doc.layout]);
  for (const chart of visible) {
    const group = source?.link_group ?? null;
    if (group === null || chart.link_group === group) ids.add(chart.chart_id);
  }
  return ids;
}

/**
 * Applies a change to charts.
 * @param doc Workspace.
 * @param ids Charts to change.
 * @param change Chart updater.
 * @return Updated charts.
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

/** Chart list type of the generated contract (1..8 tuple union). */
export type WorkspaceCharts = WorkspaceDocument['charts'];

/**
 * Narrows a chart array to the contract's 1..8 tuple type.
 * @param charts Charts (callers guarantee 1..8 items).
 * @return Typed chart list.
 */
export function asCharts(charts: ChartState[]): WorkspaceCharts {
  return charts as WorkspaceCharts;
}

/**
 * Toggles membership of a value in a list.
 * @param list List.
 * @param value Value.
 * @return New list.
 */
function toggle<T>(list: readonly T[] | undefined, value: T): T[] {
  const items = [...(list ?? [])];
  return items.includes(value)
    ? items.filter(item => item !== value)
    : [...items, value];
}

/**
 * Pure workspace reducer.
 * @param doc Current workspace.
 * @param action Action.
 * @param nowMs Clock for `updated_at_ms`.
 * @return Next workspace (same object when nothing changed).
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
 * Reducer body without timestamping.
 * @param doc Workspace.
 * @param action Action.
 * @return Next workspace.
 */
function reduce(
  doc: WorkspaceDocument,
  action: WorkspaceAction
): WorkspaceDocument {
  switch (action.type) {
    case 'replace':
      return action.workspace;
    case 'setLayout': {
      const needed = LAYOUT_PANES[action.layout];
      const charts = [...doc.charts];
      const template =
        charts.find(chart => chart.chart_id === doc.active_chart_id) ??
        charts[0];
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
      const visibleIds = charts.slice(0, needed).map(chart => chart.chart_id);
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
                  ? {...layer, visible: action.visible}
                  : layer
              )
            : [
                ...chart.indicator_layers,
                {plugin_id: action.pluginId, visible: action.visible},
              ];
          return {...chart, indicator_layers: layers};
        }),
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
 * Charts visible in the current layout.
 * @param doc Workspace.
 * @return Visible chart states.
 */
export function visibleCharts(doc: WorkspaceDocument): ChartState[] {
  return doc.charts.slice(0, LAYOUT_PANES[doc.layout]);
}

/**
 * Returns the active chart state.
 * @param doc Workspace.
 * @return Active chart (falls back to the first chart).
 */
export function activeChart(doc: WorkspaceDocument): ChartState {
  const chart =
    doc.charts.find(c => c.chart_id === doc.active_chart_id) ?? doc.charts[0];
  if (!chart) throw new Error('Workspace has no charts');
  return chart;
}
