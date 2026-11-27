/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Pinned Python study lifecycle and TradingView-style legends. */

'use client';
import type {ChartPort} from '@joseph/chart-core';
import type {ChartState, ScriptResult} from '@joseph/contracts';
import {layerVisible} from '@joseph/terminal-shell';
import {Braces, Eye, EyeOff, Settings2, X} from 'lucide-react';
import {useEffect, useRef, useState, type ReactElement} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {useIndicatorPlugins} from './indicator-plugins';
import {pythonCanvas} from './python-painter';
import {
  editPython,
  pythonAttachments,
  pythonState,
  savePythonAttachments,
  type PythonAttachment,
} from './python-state';
import {useStore} from './react-bindings';
import {ScriptDialog} from './script-dialog';
import {useRuntime} from './runtime-context';
import styles from './script-ui.module.css';

/** Public built-ins; external packages never imply permission to read source. */
const PUBLIC_SOURCE = new Set([
  'sma',
  'ema',
  'rsi',
  'atr',
  'volume_delta',
  'pivot_points',
  'anchored_vwap',
  'bollinger_bands',
  'regression_channel',
  'market_structure',
  'liquidity_sweeps',
  'imbalance',
  'order_blocks',
]);

/**
 * Executes visible pinned sources when closed bars or input configurations change.
 * @param port Mounted chart renderer, or null before mount.
 * @param chart Current chart identity, symbol and timeframe.
 * @param runtime Authenticated terminal runtime.
 * @param closedBarMs Latest confirmed source bar, or null while loading.
 * @return Per-study execution errors for visible chart diagnostics.
 */
export function usePythonStudies(
  port: ChartPort | null,
  chart: ChartState,
  runtime: TerminalRuntime,
  closedBarMs: number | null
): string[] {
  const workspace = useStore(runtime.workspace);
  const state = pythonState(runtime);
  const [errors, setErrors] = useState<string[]>([]);
  const [liveTick, setLiveTick] = useState(0);
  const completed = useRef(new Map<string, string>());
  const rows = pythonAttachments(runtime, chart.chart_id).filter(
    row => row.visible
  );
  const fingerprint = JSON.stringify(rows);
  void workspace;
  const live = rows.some(row => row.kind === 'indicator');
  useEffect(() => {
    if (!live || !port || runtime.config.demo) return;
    const timer = setInterval(() => void setLiveTick(value => value + 1), 5000);
    return () => void clearInterval(timer);
  }, [live, port, runtime]);
  useEffect(() => {
    if (!port || runtime.config.demo || closedBarMs === null) return;
    let disposed = false;
    const cleanups: Array<() => void> = [];
    const failures: string[] = [];
    setErrors([]);
    const attachments = JSON.parse(fingerprint) as PythonAttachment[];
    const execute = async () => {
      for (const row of attachments) {
        if (disposed) return;
        const key = `${chart.chart_id}:${row.script_id}`;
        try {
          const stamp = JSON.stringify([
            row.version,
            row.inputs,
            chart.instrument_id,
            chart.timeframe,
            closedBarMs,
            row.kind === 'indicator' ? liveTick : 0,
          ]);
          const cached = state.results.get()[key];
          let result: ScriptResult;
          if (cached && completed.current.get(key) === stamp) result = cached;
          else {
            result = await state.client.run(row.script_id, {
              version: row.version,
              instrument_id: chart.instrument_id,
              tf: chart.timeframe,
              limit: 2000,
              inputs: row.inputs,
              include_open: row.kind === 'indicator',
            });
            completed.current.set(key, stamp);
          }
          if (disposed) return;
          state.results.update(all => ({...all, [key]: result}));
          const canvas = pythonCanvas(`python:${key}`, result, row.colors);
          if (result.overlay) cleanups.push(port.addCanvasLayer(canvas));
          else {
            const id = `python:${key}`;
            const values = result.plots.flatMap(plot =>
              plot.points.flatMap(point =>
                point.value === null ? [] : [point.value]
              )
            );
            for (const visual of [
              ...(result.visuals ?? []),
              ...(result.bar_visuals ?? []),
            ]) {
              for (const field of [
                'price',
                'from_price',
                'to_price',
                'lower',
                'upper',
                'open',
                'high',
                'low',
                'close',
              ]) {
                const value = visual.data[field];
                if (typeof value === 'number' && Number.isFinite(value)) {
                  values.push(value);
                }
              }
            }
            const range = values.length
              ? {
                  min: values.reduce(
                    (minimum, value) => Math.min(minimum, value),
                    Infinity
                  ),
                  max: values.reduce(
                    (maximum, value) => Math.max(maximum, value),
                    -Infinity
                  ),
                }
              : {min: 0, max: 1};
            if (range.min === range.max) {
              range.min -= 1;
              range.max += 1;
            }
            port.setStudyPane({
              id,
              title: row.name,
              style: 'line',
              points: (result.plots[0]?.points ?? []).flatMap(point =>
                point.value === null
                  ? []
                  : [{timeMs: point.time_ms, value: point.value}]
              ),
              caption: `${result.data_source} · v${row.version}`,
              range,
              canvas,
            });
            cleanups.push(() => void port.removeStudyPane(id));
            if (result.fills.length) {
              cleanups.push(
                port.addCanvasLayer(
                  pythonCanvas(
                    `${id}:fills`,
                    {
                      ...result,
                      overlay: true,
                      plots: [],
                      visuals: [],
                      bar_visuals: [],
                    },
                    row.colors
                  )
                )
              );
            }
          }
          port.invalidate();
        } catch (failure) {
          if (!disposed) {
            failures.push(
              `${row.name}: ${failure instanceof Error ? failure.message : 'Script failed'}`
            );
            setErrors([...failures]);
          }
        }
      }
    };
    void execute();
    return () => {
      disposed = true;
      for (const cleanup of cleanups) {
        cleanup();
      }
    };
  }, [
    port,
    chart.chart_id,
    chart.instrument_id,
    chart.timeframe,
    runtime,
    state,
    fingerprint,
    closedBarMs,
    liveTick,
  ]);
  return errors;
}

/**
 * Renders settings for one pinned managed study with editable JSON and colors.
 * @param props Target chart, current attachment and close callback.
 * @return Settings modal preserving the source version pin.
 */
function PythonSettings({
  chartId,
  row,
  onClose,
}: {
  chartId: string;
  row: PythonAttachment;
  onClose: () => void;
}): ReactElement {
  const runtime = useRuntime();
  const [inputs, setInputs] = useState(JSON.stringify(row.inputs, null, 2));
  const [colors, setColors] = useState(JSON.stringify(row.colors, null, 2));
  const [error, setError] = useState<string | null>(null);
  return (
    <ScriptDialog title={`${row.name} settings`} onClose={onClose}>
      <p>Version {row.version} · Inputs and Style apply to this chart only.</p>
      <label className={styles.field}>
        Inputs (JSON object)
        <textarea
          className="j-input"
          value={inputs}
          onChange={event => void setInputs(event.target.value)}
          rows={8}
        />
      </label>
      <label className={styles.field}>
        Style colors (plot name → hex color)
        <textarea
          className="j-input"
          value={colors}
          onChange={event => void setColors(event.target.value)}
          rows={4}
        />
      </label>
      {error && <p role="alert">{error}</p>}
      <button
        type="button"
        className="j-btn"
        onClick={() => {
          try {
            const parsed: unknown = JSON.parse(inputs);
            const palette: unknown = JSON.parse(colors);
            if (
              !parsed ||
              Array.isArray(parsed) ||
              typeof parsed !== 'object' ||
              !palette ||
              Array.isArray(palette) ||
              typeof palette !== 'object' ||
              Object.values(palette).some(
                value =>
                  typeof value !== 'string' || !/^#[0-9a-f]{6}$/i.test(value)
              )
            ) {
              throw new Error(
                'Inputs must be an object; styles must map plot names to #rrggbb colors.'
              );
            }
            savePythonAttachments(
              runtime,
              chartId,
              pythonAttachments(runtime, chartId).map(item =>
                item.script_id === row.script_id
                  ? {
                      ...item,
                      inputs: parsed as Record<string, unknown>,
                      colors: palette as Record<string, string>,
                    }
                  : item
              )
            );
            onClose();
          } catch (failure) {
            setError(
              failure instanceof Error ? failure.message : 'Invalid settings'
            );
          }
        }}
      >
        Apply settings
      </button>
    </ScriptDialog>
  );
}

/**
 * Displays names and settings/source/hide/remove controls at the chart's top left.
 * @param props Current chart and backend script errors.
 * @return Legend rows; protected external source is explicitly unavailable.
 */
export function IndicatorLegend({
  chart,
  errors,
}: {
  chart: ChartState;
  errors: readonly string[];
}): ReactElement {
  const runtime = useRuntime();
  const plugins = useIndicatorPlugins();
  const workspace = useStore(runtime.workspace);
  const drawingTool = useStore(runtime.drawing.activeTool);
  const [source, setSource] = useState<{name: string; text: string} | null>(
    null
  );
  const [settings, setSettings] = useState<PythonAttachment | null>(null);
  const managed = pythonAttachments(runtime, chart.chart_id);
  void workspace;
  const builtins = (plugins ?? []).filter(info => {
    const layer = chart.indicator_layers.find(
      item => item.plugin_id === info.plugin_id
    );
    return (
      info.display.layer &&
      !layer?.removed &&
      (layer?.present ||
        layerVisible(chart, info.plugin_id, info.display.default_visible))
    );
  });
  const readSource = (id: string, name: string) => {
    setSource({name, text: 'Loading source…'});
    void pythonState(runtime)
      .client.source(id)
      .then(
        value => void setSource({name, text: value.source}),
        failure =>
          void setSource({
            name,
            text:
              failure instanceof Error ? failure.message : 'Source unavailable',
          })
      );
  };
  return (
    <>
      <div
        className={styles.legend}
        aria-label="Active indicators"
        data-drawing-active={drawingTool !== null}
      >
        {builtins.map(info => {
          const shown = layerVisible(
            chart,
            info.plugin_id,
            info.display.default_visible
          );
          return (
            <div
              key={info.plugin_id}
              className={`${styles.legendRow} ${shown ? '' : styles.hidden}`}
              data-testid={`legend-${info.plugin_id}`}
            >
              <span>{info.display_name}</span>
              <button
                type="button"
                className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                aria-label={`${info.display_name} settings`}
                onClick={() =>
                  void runtime.indicatorSettings.set({
                    chartId: chart.chart_id,
                    pluginId: info.plugin_id,
                  })
                }
              >
                <Settings2 size={13} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                aria-label={`${info.display_name} source`}
                disabled={
                  runtime.config.demo || !PUBLIC_SOURCE.has(info.plugin_id)
                }
                title={
                  runtime.config.demo
                    ? 'Connect a backend to read public Python source'
                    : PUBLIC_SOURCE.has(info.plugin_id)
                      ? 'Open public source'
                      : 'Source unavailable for this plugin'
                }
                onClick={() =>
                  void readSource(info.plugin_id, info.display_name)
                }
              >
                <Braces size={13} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                aria-label={`${shown ? 'Hide' : 'Show'} ${info.display_name}`}
                onClick={() =>
                  void runtime.dispatch({
                    type: 'setIndicatorLayer',
                    chartId: chart.chart_id,
                    pluginId: info.plugin_id,
                    visible: !shown,
                  })
                }
              >
                {shown ? (
                  <Eye size={13} aria-hidden="true" />
                ) : (
                  <EyeOff size={13} aria-hidden="true" />
                )}
              </button>
              <button
                type="button"
                className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                aria-label={`Remove ${info.display_name}`}
                onClick={() =>
                  void runtime.dispatch({
                    type: 'removeIndicatorLayer',
                    chartId: chart.chart_id,
                    pluginId: info.plugin_id,
                  })
                }
              >
                <X size={13} aria-hidden="true" />
              </button>
            </div>
          );
        })}
        {managed.map(row => (
          <div
            key={row.script_id}
            className={`${styles.legendRow} ${row.visible ? '' : styles.hidden}`}
          >
            <span>
              {row.name} · v{row.version}
            </span>
            <button
              type="button"
              className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
              aria-label={`${row.name} settings`}
              onClick={() => void setSettings(row)}
            >
              <Settings2 size={13} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
              aria-label={`${row.name} source`}
              onClick={() =>
                void editPython(runtime, row.script_id, row.version).catch(
                  failure =>
                    void runtime.notifier.notify(String(failure), 'error')
                )
              }
            >
              <Braces size={13} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
              aria-label={`${row.visible ? 'Hide' : 'Show'} ${row.name}`}
              onClick={() =>
                void savePythonAttachments(
                  runtime,
                  chart.chart_id,
                  managed.map(item =>
                    item.script_id === row.script_id
                      ? {...item, visible: !item.visible}
                      : item
                  )
                )
              }
            >
              {row.visible ? (
                <Eye size={13} aria-hidden="true" />
              ) : (
                <EyeOff size={13} aria-hidden="true" />
              )}
            </button>
            <button
              type="button"
              className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
              aria-label={`Remove ${row.name}`}
              onClick={() =>
                void savePythonAttachments(
                  runtime,
                  chart.chart_id,
                  managed.filter(item => item.script_id !== row.script_id)
                )
              }
            >
              <X size={13} aria-hidden="true" />
            </button>
          </div>
        ))}
        {errors.map(error => (
          <div key={error} className={styles.legendRow} role="alert">
            {error}
          </div>
        ))}
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
      {settings && (
        <PythonSettings
          chartId={chart.chart_id}
          row={settings}
          onClose={() => void setSettings(null)}
        />
      )}
    </>
  );
}
