/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Chart studies: one system for built-in, plugin and owned
 * Python scripts. Every visible study of a chart is run by the engine (or
 * the DEMO evaluator) in batches over the chart's candles once the chart
 * has loaded them, refreshed every few seconds while live, and drawn
 * through the Python painter: overlay studies on the price pane, the
 * others in their own study pane. The TradingView-style legend lists each
 * study with its inputs and settings, source, visibility and remove
 * controls.
 */

'use client';
import type {ChartPort, StudyPoint} from '@joseph/chart-core';
import type {
  CatalogScript,
  ChartState,
  ScriptBatchRun,
  ScriptResult,
} from '@joseph/contracts';
import type {StudyState} from '@joseph/terminal-shell';
import {Braces, Eye, EyeOff, Settings2, X} from 'lucide-react';
import {useEffect, useRef, useState, type ReactElement} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {pythonCanvas} from './python-painter';
import {editPython, pythonState} from './python-state';
import {useStore} from './react-bindings';
import {ScriptDialog} from './script-dialog';
import {useRuntime} from './runtime-context';
import styles from './script-ui.module.css';
import {
  catalogEntry,
  isLegacyPlaceholder,
  legacyPluginEntry,
  studyCatalog,
  studyName,
  useStudyCatalog,
} from './study-catalog';

/** Refresh interval of live indicator studies (5 s). */
const LIVE_REFRESH_MS = 5000;

/**
 * Bars a study run reads when the chart has loaded fewer (studies get warm
 * history); runs read the chart's loaded bars, at most the engine's limit,
 * so they reuse candles the engine already serves the chart.
 */
export const STUDY_BARS = 500;

/** Most bars one study run reads (the engine's run limit). */
export const MAX_STUDY_BARS = 5000;

/** Most studies per batched run (the engine's batch limit). */
const BATCH_SIZE = 16;

/** Fields of drawings that hold prices (study pane ranges). */
const PRICE_FIELDS = [
  'price',
  'from_price',
  'to_price',
  'lower',
  'upper',
  'open',
  'high',
  'low',
  'close',
] as const;

/**
 * Returns the results-store key of a study.
 * @param chartId Chart id.
 * @param studyId Study id.
 * @return `<chart id>:<study id>`.
 */
export function studyKey(chartId: string, studyId: string): string {
  return `${chartId}:${studyId}`;
}

/**
 * Cuts a result to the bars before a replay cursor.
 * @param result Script result.
 * @param atMs Replay time, or null when live.
 * @return The result as of the cursor: later plot points and drawings that
 *     start later are dropped.
 */
export function resultAsOf(
  result: ScriptResult,
  atMs: number | null
): ScriptResult {
  if (atMs === null) return result;
  const before = (value: unknown) => typeof value !== 'number' || value < atMs;
  return {
    ...result,
    plots: result.plots.map(plot => ({
      ...plot,
      points: plot.points.filter(point => point.time_ms < atMs),
    })),
    visuals: (result.visuals ?? []).filter(
      visual => before(visual.data.from_ms) && before(visual.data.time_ms)
    ),
    bar_visuals: (result.bar_visuals ?? []).filter(visual =>
      before(visual.data.time_ms)
    ),
    alerts: (result.alerts ?? []).filter(alert => alert.time_ms < atMs),
    fills: result.fills.filter(fill => fill.time_ms < atMs),
  };
}

/**
 * Finds the plot a study pane draws natively: the first drawn histogram.
 * @param result Script result.
 * @return Plot name, or null when every plot is painted on the canvas.
 */
function nativePlot(result: ScriptResult): string | null {
  const plot = result.plots.find(item => {
    const style = result.plot_styles?.[item.name] ?? {};
    return style.display !== 'data_window';
  });
  if (!plot) return null;
  const style = result.plot_styles?.[plot.name]?.style;
  return style === 'histogram' || style === 'columns' ? plot.name : null;
}

/**
 * Computes the value range of a study pane from its drawn plots and levels.
 * @param result Script result.
 * @return Finite bounds, widened when flat.
 */
function paneRange(result: ScriptResult): {min: number; max: number} {
  let min = Infinity;
  let max = -Infinity;
  const add = (value: unknown) => {
    if (typeof value === 'number' && Number.isFinite(value)) {
      min = Math.min(min, value);
      max = Math.max(max, value);
    }
  };
  for (const plot of result.plots) {
    if (result.plot_styles?.[plot.name]?.display === 'data_window') continue;
    for (const point of plot.points) {
      add(point.value);
    }
  }
  for (const visual of [
    ...(result.visuals ?? []),
    ...(result.bar_visuals ?? []),
  ]) {
    for (const field of PRICE_FIELDS) {
      add(visual.data[field]);
    }
  }
  if (min === Infinity) return {min: 0, max: 1};
  return min === max ? {min: min - 1, max: max + 1} : {min, max};
}

/**
 * Draws one study result on a chart.
 * @param port Chart renderer.
 * @param id Layer or pane id.
 * @param title Pane title.
 * @param result Result as of the replay cursor.
 * @param colors User plot colors.
 * @return Disposer removing what was added.
 */
function renderStudy(
  port: ChartPort,
  id: string,
  title: string,
  result: ScriptResult,
  colors: Readonly<Record<string, string>>
): () => void {
  if (result.overlay) {
    return port.addCanvasLayer(pythonCanvas(id, result, colors));
  }
  const native = nativePlot(result);
  const first =
    result.plots.find(plot => plot.name === native) ??
    result.plots.find(
      plot => result.plot_styles?.[plot.name]?.display !== 'data_window'
    );
  const points: StudyPoint[] = (first?.points ?? []).flatMap(point =>
    point.value === null
      ? []
      : [
          {
            timeMs: point.time_ms,
            value: point.value,
            ...(native
              ? {
                  tone: point.value >= 0 ? ('bid' as const) : ('ask' as const),
                  opacity: (point.color ?? '').length === 9 ? 0.4 : 0.75,
                }
              : {}),
          },
        ]
  );
  port.setStudyPane({
    id,
    title,
    style: native ? 'histogram' : 'line',
    points,
    caption: result.data_source,
    range: paneRange(result),
    canvas: pythonCanvas(id, result, colors, native),
  });
  const extra = result.fills.length
    ? port.addCanvasLayer(
        pythonCanvas(
          `${id}:fills`,
          {...result, overlay: true, plots: [], visuals: [], bar_visuals: []},
          colors
        )
      )
    : () => undefined;
  return () => {
    extra();
    port.removeStudyPane(id);
  };
}

/**
 * Runs and draws the visible studies of a chart. Runs start once the chart
 * has its first closed bar, are batched per role and re-run when a bar
 * closes, the studies change and, for indicators, every few seconds while
 * live. A study keeps its last drawing until its next result arrives.
 * @param port Mounted chart renderer, or null before mount.
 * @param chart Chart state (instrument, timeframe and studies).
 * @param runtime Terminal runtime.
 * @param closedBarMs Latest confirmed bar, or null while loading.
 * @param atMs Replay cursor, or null when live.
 * @param loadedBars Bars the chart has loaded; runs read as many.
 * @return Errors by study id.
 */
export function useStudyRuns(
  port: ChartPort | null,
  chart: ChartState,
  runtime: TerminalRuntime,
  closedBarMs: number | null,
  atMs: number | null = null,
  loadedBars = STUDY_BARS
): Readonly<Record<string, string>> {
  const state = pythonState(runtime);
  const [errors, setErrors] = useState<Readonly<Record<string, string>>>({});
  const [liveTick, setLiveTick] = useState(0);
  const drawn = useRef(new Map<string, () => void>());
  const rows = chart.studies.filter(study => study.visible);
  const fingerprint = JSON.stringify(rows);
  const live = atMs === null && rows.some(row => row.kind === 'indicator');
  const {chart_id: chartId, instrument_id: instrumentId, timeframe} = chart;
  const limit = Math.min(MAX_STUDY_BARS, Math.max(STUDY_BARS, loadedBars));
  const catalog = useStore(studyCatalog(runtime));
  // Migrated plugin layers become the installed plugin script with the same
  // ID once the catalog is known; the replacement keeps the study id.
  useEffect(() => {
    if (!catalog) return;
    for (const study of chart.studies) {
      const entry = legacyPluginEntry(catalog, study);
      if (!entry) continue;
      runtime.dispatch({
        type: 'addStudy',
        chartId,
        study: {...study, script_id: entry.script_id, name: entry.short_title},
      });
    }
  }, [catalog, chart.studies, chartId, runtime]);
  useEffect(() => {
    if (!live || !port) return;
    const timer = setInterval(
      () => void setLiveTick(value => value + 1),
      LIVE_REFRESH_MS
    );
    return () => void clearInterval(timer);
  }, [live, port]);
  // Drawings of studies that are gone or hidden are removed at once.
  useEffect(() => {
    const wanted = new Set(
      (JSON.parse(fingerprint) as StudyState[]).map(row => row.study_id)
    );
    for (const [id, dispose] of drawn.current) {
      if (!wanted.has(id)) {
        dispose();
        drawn.current.delete(id);
      }
    }
    port?.invalidate();
  }, [port, fingerprint]);
  useEffect(
    () => () => {
      for (const dispose of drawn.current.values()) {
        dispose();
      }
      drawn.current.clear();
    },
    [port, chartId, instrumentId, timeframe]
  );
  useEffect(() => {
    if (!port || closedBarMs === null) return;
    const failures: Record<string, string> = {};
    const studies = (JSON.parse(fingerprint) as StudyState[]).filter(row => {
      if (!isLegacyPlaceholder(row)) return true;
      failures[row.study_id] =
        `Plugin script "${row.name}" is not installed on the market engine`;
      return false;
    });
    if (studies.length === 0) {
      setErrors({...failures});
      return;
    }
    let disposed = false;
    const windowFrom = port.serialize().visibleRange?.fromMs;
    const groups: StudyState[][] = [];
    for (const includeOpen of [true, false]) {
      const group = studies.filter(
        row => (row.kind === 'indicator') === includeOpen
      );
      for (let start = 0; start < group.length; start += BATCH_SIZE) {
        groups.push(group.slice(start, start + BATCH_SIZE));
      }
    }
    const apply = (row: StudyState, result: ScriptResult) => {
      state.results.update(all => ({
        ...all,
        [studyKey(chartId, row.study_id)]: result,
      }));
      drawn.current.get(row.study_id)?.();
      drawn.current.set(
        row.study_id,
        renderStudy(
          port,
          `study:${chartId}:${row.study_id}`,
          row.name,
          resultAsOf(result, atMs),
          row.colors as Record<string, string>
        )
      );
    };
    const run = async (group: StudyState[]) => {
      const body: ScriptBatchRun = {
        instrument_id: instrumentId,
        tf: timeframe,
        limit,
        include_open: group[0]?.kind === 'indicator',
        ...(windowFrom !== undefined ? {window_from_ms: windowFrom} : {}),
        // Groups hold at most BATCH_SIZE studies, the contract's bound.
        runs: group.map(row => ({
          script_id: row.script_id,
          ...(row.version > 0 ? {version: row.version} : {}),
          inputs: row.inputs,
        })) as unknown as ScriptBatchRun['runs'],
      };
      try {
        const answer = await runtime.clients.studies.runBatch(body);
        if (disposed) return;
        answer.results.forEach((entry, index) => {
          const row = group[index];
          if (!row) return;
          if (entry.result) apply(row, entry.result);
          else failures[row.study_id] = entry.error ?? 'Study failed';
        });
      } catch (failure) {
        if (disposed) return;
        for (const row of group) {
          failures[row.study_id] =
            failure instanceof Error ? failure.message : 'Study failed';
        }
      }
    };
    void Promise.all(groups.map(run)).then(() => {
      if (disposed) return;
      setErrors({...failures});
      port.invalidate();
    });
    return () => {
      disposed = true;
    };
  }, [
    port,
    chartId,
    instrumentId,
    timeframe,
    runtime,
    state,
    fingerprint,
    closedBarMs,
    liveTick,
    atMs,
    limit,
  ]);
  return errors;
}

/**
 * Summarizes a study's non-style inputs like TradingView's legend.
 * @param study Chart study.
 * @param entry Catalog entry, when the study runs a catalog script.
 * @return Space-separated input values, e.g. `20 close 0`.
 */
export function inputsSummary(
  study: StudyState,
  entry: CatalogScript | undefined
): string {
  const values: Record<string, unknown> = {
    ...Object.fromEntries(
      Object.entries(entry?.inputs ?? {}).map(([key, spec]) => [
        key,
        spec?.default,
      ])
    ),
    ...(study.inputs as Record<string, unknown>),
  };
  return Object.entries(values)
    .filter(([key, value]) => {
      const spec = entry?.inputs[key];
      if (spec?.group === 'Style') return false;
      if (typeof value === 'number') return !(key.endsWith('_ms') && !value);
      return typeof value === 'string' && !value.startsWith('#');
    })
    .map(([, value]) => String(value))
    .join(' ');
}

/**
 * Displays the chart's studies at its top left with settings, source,
 * visibility and remove controls.
 * @param props Current chart and study errors by study id.
 * @return Legend rows and the source dialog.
 */
export function StudyLegend({
  chart,
  errors,
}: {
  chart: ChartState;
  errors: Readonly<Record<string, string>>;
}): ReactElement {
  const runtime = useRuntime();
  const catalog = useStudyCatalog();
  const drawingTool = useStore(runtime.drawing.activeTool);
  const [source, setSource] = useState<{name: string; text: string} | null>(
    null
  );
  const chartId = chart.chart_id;
  const readSource = (study: StudyState, name: string) => {
    if (catalogEntry(catalog, study) || study.version === 0) {
      setSource({name, text: 'Loading source…'});
      void runtime.clients.studies.catalogScript(study.script_id).then(
        script =>
          void setSource({
            name,
            text: script.source ?? 'Source unavailable for this plugin',
          }),
        failure =>
          void setSource({
            name,
            text:
              failure instanceof Error ? failure.message : 'Source unavailable',
          })
      );
      return;
    }
    void editPython(runtime, study.script_id, study.version).catch(
      failure => void runtime.notifier.notify(String(failure), 'error')
    );
  };
  return (
    <>
      <div
        className={styles.legend}
        aria-label="Active indicators"
        data-drawing-active={drawingTool !== null}
      >
        {chart.studies.map(study => {
          const entry = catalogEntry(catalog, study);
          const name = studyName(catalog, study);
          const summary = inputsSummary(study, entry);
          const owned = study.version > 0;
          const sourceOff =
            !owned &&
            (runtime.clients.demo || entry?.source_available === false);
          const error = errors[study.study_id];
          return (
            <div key={study.study_id}>
              <div
                className={`${styles.legendRow} ${study.visible ? '' : styles.hidden}`}
                data-testid={`legend-${study.study_id}`}
              >
                <span>
                  {name}
                  {owned && ` · v${study.version}`}
                  {summary && <span className="j-muted"> {summary}</span>}
                </span>
                <button
                  type="button"
                  className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                  aria-label={`${name} settings`}
                  data-testid={`indicator-settings-${study.study_id}`}
                  onClick={() =>
                    void runtime.indicatorSettings.set({
                      chartId,
                      studyId: study.study_id,
                    })
                  }
                >
                  <Settings2 size={13} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                  aria-label={`${name} source`}
                  disabled={sourceOff}
                  title={
                    owned
                      ? 'Edit source'
                      : runtime.clients.demo
                        ? 'Connect a backend to read public Python source'
                        : sourceOff
                          ? 'Source unavailable for this plugin'
                          : 'Open public source'
                  }
                  onClick={() => void readSource(study, name)}
                >
                  <Braces size={13} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                  aria-label={`${study.visible ? 'Hide' : 'Show'} ${name}`}
                  onClick={() =>
                    void runtime.dispatch({
                      type: 'updateStudy',
                      chartId,
                      studyId: study.study_id,
                      patch: {visible: !study.visible},
                    })
                  }
                >
                  {study.visible ? (
                    <Eye size={13} aria-hidden="true" />
                  ) : (
                    <EyeOff size={13} aria-hidden="true" />
                  )}
                </button>
                <button
                  type="button"
                  className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                  aria-label={`Remove ${name}`}
                  onClick={() =>
                    void runtime.dispatch({
                      type: 'removeStudy',
                      chartId,
                      studyId: study.study_id,
                    })
                  }
                >
                  <X size={13} aria-hidden="true" />
                </button>
              </div>
              {error && study.visible && (
                <div className={styles.legendRow} role="alert">
                  {name}: {error}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {source && (
        <ScriptDialog
          title={`${source.name} source`}
          onClose={() => void setSource(null)}
        >
          <textarea
            className={styles.source}
            aria-label="Public indicator source"
            value={source.text}
            readOnly
          />
        </ScriptDialog>
      )}
    </>
  );
}
