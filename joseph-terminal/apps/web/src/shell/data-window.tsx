/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Data window: values of the bar under the crosshair (OHLCV,
 * change, when the bar closes, the engine's Delta % with whether it is the
 * true delta or an estimate, and compared closes) plus the engine values of
 * engine-backed drawings (anchored VWAP, regression channel). Display only.
 */

'use client';
import type {ChartBar} from '@joseph/chart-core';
import type {BarCalendar, Instrument, Timeframe} from '@joseph/contracts';
import {seriesAsOf} from '@joseph/drawing-core';
import {DELTA_METHOD_LABELS} from '@joseph/indicator-viz';
import type {ReactElement} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import type {CompareEntry, DeltaByTime} from './chart-pane-hooks';
import {
  formatBarClose,
  formatBarTime,
  formatDecimal,
  formatInstrumentPrice,
  formatNumber,
  formatPrice,
  formatVolume,
  lotSizeForVolume,
} from './format';
import {useStore} from './react-bindings';

/** One drawing value row. */
export interface DrawingRow {
  /** Unique key: drawing id and row index. */
  key: string;
  /** Row label, e.g. `VWAP`. */
  label: string;
  /** Formatted value. */
  value: string;
}

/** Data window props. */
export interface DataWindowProps {
  /** Bar under the crosshair (or the latest bar). */
  bar: ChartBar | null;
  /** Chart timeframe (bar time format and calendar-aware close). */
  timeframe: Timeframe;
  /** Session calendar (exchange time and session bar ends; UTC default). */
  calendar?: BarCalendar | null;
  /** Instrument (ladder precision, yields in %, share and lot volumes). */
  instrument?: Instrument | null;
  /** Previous bar's close (for the change), or null for the first bar. */
  previousClose: number | null;
  /** Tick size for price precision while the instrument is unknown. */
  tickSize: string | null;
  /** Engine delta bars by bar start, when the pane is shown. */
  delta: DeltaByTime | null;
  /** Compared instruments. */
  compare: readonly CompareEntry[];
  /** Display label per compared instrument. */
  label: (instrumentId: string) => string;
  /** Values of drawings on the chart (engine-backed tools). */
  drawingRows?: readonly DrawingRow[];
}

/**
 * Renders the data window: a labelled `aside` with the values as a
 * description list. It is display only and never takes the pointer.
 * @param props Props.
 * @return Panel.
 */
export function DataWindow({
  bar,
  timeframe,
  calendar = null,
  instrument = null,
  previousClose,
  tickSize,
  delta,
  compare,
  label,
  drawingRows = [],
}: DataWindowProps): ReactElement {
  const price = (value: number | undefined) =>
    value === undefined
      ? '—'
      : instrument
        ? formatInstrumentPrice(String(value), instrument)
        : formatPrice(String(value), tickSize);
  const lot = lotSizeForVolume(instrument);
  const unitTitle = lot
    ? `Volume in board lots (張, 1 張 = ${lot.toLocaleString('en-US')} shares)`
    : undefined;
  const change =
    bar && previousClose
      ? ((bar.close - previousClose) / previousClose) * 100
      : null;
  const deltaBar = bar ? delta?.get(bar.timeMs) : undefined;
  const deltaValue = deltaBar?.value;
  // Rows are [label, value, tone class, React key, tooltip].
  const rows: Array<[string, string, string?, string?, string?]> = bar
    ? [
        ['Open', price(bar.open)],
        ['High', price(bar.high)],
        ['Low', price(bar.low)],
        ['Close', price(bar.close), bar.close >= bar.open ? 'j-pos' : 'j-neg'],
        [
          'Change',
          change === null
            ? '—'
            : `${change >= 0 ? '+' : ''}${formatNumber(change, 2)}%`,
          change === null ? undefined : change >= 0 ? 'j-pos' : 'j-neg',
        ],
        [
          instrument?.key.market_type === 'rate' ? '' : 'Volume',
          instrument
            ? formatVolume(bar.volume, instrument)
            : formatNumber(bar.volume, 2),
          undefined,
          undefined,
          unitTitle,
        ],
        ['Closes', formatBarClose(timeframe, bar.timeMs, calendar)],
      ]
    : [];
  if (bar && delta) {
    rows.push([
      'Delta %',
      deltaValue === undefined ? '—' : `${formatNumber(deltaValue, 2)}%`,
      deltaValue === undefined
        ? undefined
        : deltaValue >= 0
          ? 'j-pos'
          : 'j-neg',
    ]);
    if (deltaBar) {
      rows.push([
        'Delta source',
        DELTA_METHOD_LABELS[deltaBar.method],
        deltaBar.method === 'estimate' ? 'j-muted' : undefined,
      ]);
      if (deltaBar.buyVolume !== null && deltaBar.sellVolume !== null) {
        const volume = (value: string) =>
          instrument
            ? formatVolume(Number(value), instrument)
            : formatDecimal(value, 2);
        rows.push(['Buy volume', volume(deltaBar.buyVolume), 'j-pos']);
        rows.push(['Sell volume', volume(deltaBar.sellVolume), 'j-neg']);
      }
    }
  }
  // Yields have no volume: their volume row has an empty label.
  const visible = rows.filter(([name]) => name !== '');
  rows.splice(0, rows.length, ...visible);
  for (const entry of compare) {
    const value = bar ? entry.values.get(bar.timeMs) : undefined;
    rows.push([
      label(entry.instrumentId),
      entry.error
        ? 'unavailable'
        : value === undefined
          ? '—'
          : formatNumber(value, 4),
    ]);
  }
  for (const row of drawingRows) {
    rows.push([row.label, row.value, undefined, row.key]);
  }
  return (
    <aside
      className="data-window j-tabular"
      aria-label="Data window"
      data-testid="data-window"
    >
      <p className="data-window__time">
        {bar ? formatBarTime(timeframe, bar.timeMs, calendar) : 'No bar'}
      </p>
      <dl>
        {rows.map(([name, value, tone, key, title]) => (
          <div key={key ?? name} title={title}>
            <dt>{name}</dt>
            <dd className={tone}>{value}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}

/**
 * Collects data-window rows from the chart's engine-backed drawings.
 * @param runtime Runtime holding the chart's drawing controller and series.
 * @param chartId Chart id.
 * @param barTimeMs Start of the bar under the crosshair, UTC ms.
 * @param tickSize Tick size for price precision.
 * @param atMs Replay as-of time, or null when live.
 * @return Rows in z order; empty when no visible drawing has data rows.
 */
export function drawingDataRows(
  runtime: TerminalRuntime,
  chartId: string,
  barTimeMs: number,
  tickSize: string | null,
  atMs: number | null
): DrawingRow[] {
  const controller = runtime.drawing.controllers.get(chartId);
  const sync = runtime.drawing.series.get(chartId);
  const rows: DrawingRow[] = [];
  for (const doc of controller?.visibleDrawings() ?? []) {
    const tool = runtime.tools.get(doc.tool_id);
    if (!tool?.dataRows) continue;
    const series = seriesAsOf(sync?.get(doc.drawing_id) ?? null, atMs);
    tool
      .dataRows(doc, barTimeMs, {series, tickSize})
      .forEach((row, i) => rows.push({key: `${doc.drawing_id}:${i}`, ...row}));
  }
  return rows;
}

/** Props of the chart's data window. */
export interface ChartDataWindowProps extends DataWindowProps {
  /** Runtime. */
  runtime: TerminalRuntime;
  /** Chart id. */
  chartId: string;
  /** Replay as-of time, or null when live. */
  atMs: number | null;
}

/**
 * Renders the data window of one chart, re-rendered when drawings or their
 * engine values change.
 * @param props Props.
 * @return Panel.
 */
export function ChartDataWindow({
  runtime,
  chartId,
  atMs,
  ...props
}: ChartDataWindowProps): ReactElement {
  useStore(runtime.drawing.revision);
  const rows = props.bar
    ? drawingDataRows(runtime, chartId, props.bar.timeMs, props.tickSize, atMs)
    : [];
  return <DataWindow {...props} drawingRows={rows} />;
}
