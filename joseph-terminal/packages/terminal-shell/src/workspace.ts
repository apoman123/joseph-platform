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

/** One study entry of a chart (contract `StudyState`). */
export type StudyState = ChartState['studies'][number];

/**
 * Studies of a new chart: the built-in EMA, volume delta and pivot points.
 * Built-in and plugin scripts carry version 0; their titles come from the
 * engine catalog once it loads, `name` is the offline fallback.
 */
export const DEFAULT_STUDIES: readonly StudyState[] = [
  {
    study_id: 'builtin:ema',
    script_id: 'builtin:ema',
    version: 0,
    kind: 'indicator',
    name: 'EMA',
    visible: true,
    inputs: {},
    colors: {},
  },
  {
    study_id: 'builtin:volume_delta',
    script_id: 'builtin:volume_delta',
    version: 0,
    kind: 'indicator',
    name: 'Delta %',
    visible: true,
    inputs: {},
    colors: {},
  },
  {
    study_id: 'builtin:pivot_points',
    script_id: 'builtin:pivot_points',
    version: 0,
    kind: 'indicator',
    name: 'Pivots',
    visible: true,
    inputs: {},
    colors: {},
  },
];

/** Maximum instruments compared on one chart. */
export const MAX_COMPARE = 3;

/** Most studies on one chart (contract limit). */
export const MAX_STUDIES = 40;

/** Fields of a study that `updateStudy` may change. */
export type StudyPatch = Partial<
  Pick<StudyState, 'visible' | 'inputs' | 'colors' | 'version' | 'name'>
>;

/**
 * Returns a study id that is unused on a chart: the script id, else the
 * script id with the first free `#n` suffix.
 * @param chart Chart receiving the study.
 * @param scriptId Script the study runs.
 * @return Unique study id.
 */
export function nextStudyId(chart: ChartState, scriptId: string): string {
  const used = new Set(chart.studies.map(study => study.study_id));
  if (!used.has(scriptId)) return scriptId;
  let index = 2;
  while (used.has(`${scriptId}#${index}`)) index += 1;
  return `${scriptId}#${index}`;
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
 * volume, `DEFAULT_STUDIES`, no link group, `AUTO` feed and no saved
 * viewport.
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
    studies: DEFAULT_STUDIES.map(study => ({
      ...study,
      inputs: {},
      colors: {},
    })),
    link_group: null,
    feed_mode: 'AUTO',
    visible_range: null,
  };
}

/**
 * Creates the default single-chart workspace: layout `1`, crosshair sync
 * only, and the default favorite timeframes.
 * @param input Workspace id, first chart and clock.
 * @return Workspace document (schema v2).
 */
export function createDefaultWorkspace(
  input: DefaultWorkspaceInput
): WorkspaceDocument {
  return {
    schema_version: 2,
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
      viewType?: 'candles' | 'stock_heatmap';
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
      /** Appends a study, or replaces the one with the same study id. */
      type: 'addStudy';
      chartId: string;
      study: StudyState;
    }
  | {
      /** Changes a study's visibility, inputs, colors, version or name. */
      type: 'updateStudy';
      chartId: string;
      studyId: string;
      patch: StudyPatch;
    }
  | {
      /** Removes a study and its settings, unlike hiding it. */
      type: 'removeStudy';
      chartId: string;
      studyId: string;
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
  'chart_type' | 'price_scale' | 'show_volume' | 'studies'
> & {session_separators?: boolean};

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
    case 'addStudy':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => {
          const exists = chart.studies.some(
            study => study.study_id === action.study.study_id
          );
          if (!exists && chart.studies.length >= MAX_STUDIES) return chart;
          const study = structuredClone(action.study);
          return {
            ...chart,
            studies: exists
              ? chart.studies.map(item =>
                  item.study_id === study.study_id ? study : item
                )
              : [...chart.studies, study],
          };
        }),
      };
    case 'updateStudy':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => ({
          ...chart,
          studies: chart.studies.map(study =>
            study.study_id === action.studyId
              ? {...study, ...structuredClone(action.patch)}
              : study
          ),
        })),
      };
    case 'removeStudy':
      return {
        ...doc,
        charts: mapCharts(doc, new Set([action.chartId]), chart => ({
          ...chart,
          studies: chart.studies.filter(
            study => study.study_id !== action.studyId
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
          studies: structuredClone(action.presentation.studies),
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
