/**
 * @fileoverview Data window: values of the bar under the crosshair (OHLCV,
 * change, the engine's Delta % and compared closes). Display only.
 */
'use client';
import type {ChartBar} from '@joseph/chart-core';
import type {ReactElement} from 'react';
import type {CompareEntry, ValueByTime} from './chart-pane-hooks';
import {formatNumber, formatPrice} from './format';

/** Data window props. */
export interface DataWindowProps {
  /** Bar under the crosshair (or the latest bar). */
  bar: ChartBar | null;
  /** Previous bar's close (for the change). */
  previousClose: number | null;
  /** Tick size for price precision. */
  tickSize: string | null;
  /** Delta % by bar start, when the pane is shown. */
  delta: ValueByTime | null;
  /** Compared instruments. */
  compare: readonly CompareEntry[];
  /** Display label per compared instrument. */
  label: (instrumentId: string) => string;
}

/**
 * Formats a full UTC timestamp of a bar.
 * @param ms Bar start.
 * @return `YYYY-MM-DD HH:MM UTC`.
 */
function barTime(ms: number): string {
  return `${new Date(ms).toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

/**
 * Data window.
 * @param props Props.
 * @return Panel.
 */
export function DataWindow({
  bar,
  previousClose,
  tickSize,
  delta,
  compare,
  label,
}: DataWindowProps): ReactElement {
  const price = (value: number | undefined) =>
    value === undefined ? '—' : formatPrice(String(value), tickSize);
  const change =
    bar && previousClose
      ? ((bar.close - previousClose) / previousClose) * 100
      : null;
  const deltaValue = bar ? delta?.get(bar.timeMs) : undefined;
  const rows: [string, string, string?][] = bar
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
        ['Volume', formatNumber(bar.volume, 2)],
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
  }
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
  return (
    <aside
      className="data-window j-tabular"
      aria-label="Data window"
      data-testid="data-window"
    >
      <p className="data-window__time">
        {bar ? barTime(bar.timeMs) : 'No bar'}
      </p>
      <dl>
        {rows.map(([name, value, tone]) => (
          <div key={name}>
            <dt>{name}</dt>
            <dd className={tone}>{value}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}
