/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview True versus estimated order-flow delta: per-bar methods
 * from `values.method`, buy/sell volumes, faint estimate columns and the
 * pane title.
 */

import type {IndicatorSeries} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  CLASSIFIED_DELTA_OPACITY,
  DELTA_METHOD_LABELS,
  ESTIMATE_DELTA_OPACITY,
  TRUE_DELTA_OPACITY,
  deltaBarsFromSeries,
  deltaMethodOf,
  deltaOpacity,
  deltaStudyFromSeries,
  deltaStudyTitle,
} from './study';

const T0 = Date.UTC(2026, 10, 22, 9);
const BAR = 300_000;

/**
 * Builds a series from per-bar values.
 * @param values Values per bar.
 * @return Series.
 */
function seriesOf(values: Array<IndicatorSeries['points'][number]['values']>) {
  return {
    instrument_id: 'binance:perp:BTCUSDT',
    timeframe: '5m',
    plugin_id: 'order_flow_pressure',
    indicator_version: '1.1.0',
    as_of_ms: T0 + values.length * BAR,
    feed_state: 'LIVE',
    points: values.map((v, i) => ({bar_start_ms: T0 + i * BAR, values: v})),
  } satisfies IndicatorSeries;
}

const MIXED = seriesOf([
  {
    delta_pct: '12.50',
    method: 'taker_volume',
    buy_volume: '56.25',
    sell_volume: '43.75',
  },
  {
    delta_pct: '-30',
    method: 'candle_direction',
    buy_volume: null,
    sell_volume: null,
  },
  {delta_pct: '5', method: 'something_new'},
]);

describe('delta method per bar', () => {
  it('calls a bar true only for taker volume', () => {
    expect(deltaMethodOf('taker_volume')).toBe('true');
    expect(deltaMethodOf('candle_direction')).toBe('estimate');
    expect(deltaMethodOf(undefined)).toBe('estimate');
    expect(deltaMethodOf('TAKER_VOLUME')).toBe('estimate');
  });

  it('keeps buy and sell volume of true bars only', () => {
    const bars = deltaBarsFromSeries(MIXED);
    expect(bars.map(b => b.method)).toEqual(['true', 'estimate', 'estimate']);
    expect(bars[0]).toMatchObject({
      value: 12.5,
      buyVolume: '56.25',
      sellVolume: '43.75',
    });
    expect(bars[1]).toMatchObject({buyVolume: null, sellVolume: null});
    expect(deltaBarsFromSeries(MIXED, T0 + BAR)).toHaveLength(1);
  });

  it('draws estimates faint and names the methods in the title', () => {
    const study = deltaStudyFromSeries(MIXED);
    expect(study.points.map(p => p.opacity)).toEqual([
      TRUE_DELTA_OPACITY,
      ESTIMATE_DELTA_OPACITY,
      ESTIMATE_DELTA_OPACITY,
    ]);
    expect(study.title).toBe('Delta % (true; faint = estimate)');
    expect(
      deltaStudyFromSeries(seriesOf([{delta_pct: 1, method: 'taker_volume'}]))
        .title
    ).toBe('Delta % (true)');
    expect(deltaStudyTitle([])).toBe('Delta %');
    // Replay hides later bars, so the title follows what is visible.
    expect(deltaStudyFromSeries(MIXED, {atMs: T0 + BAR}).title).toBe(
      'Delta % (true)'
    );
  });
});

describe('classified (Lee–Ready) delta', () => {
  const STOCK = seriesOf([
    {
      delta_pct: '8',
      method: 'trade_classification',
      buy_volume: '540',
      sell_volume: '460',
    },
    {delta_pct: '-4', method: 'candle_direction'},
  ]);

  it('labels trade classification separately from true and estimate', () => {
    expect(deltaMethodOf('trade_classification')).toBe('classified');
    expect(DELTA_METHOD_LABELS.classified).toBe('Classified (Lee–Ready)');
    expect(DELTA_METHOD_LABELS.true).toBe('True (taker volume)');
    expect(DELTA_METHOD_LABELS.estimate).toBe('Estimate');
    const [classified, estimate] = deltaBarsFromSeries(STOCK);
    expect(classified).toMatchObject({
      method: 'classified',
      buyVolume: '540',
      sellVolume: '460',
    });
    expect(estimate).toMatchObject({method: 'estimate', buyVolume: null});
  });

  it('draws classified columns between true and estimate opacity', () => {
    const study = deltaStudyFromSeries(STOCK);
    expect(study.points.map(p => p.opacity)).toEqual([
      CLASSIFIED_DELTA_OPACITY,
      ESTIMATE_DELTA_OPACITY,
    ]);
    expect(study.title).toBe('Delta % (classified; faint = estimate)');
    expect(deltaStudyTitle([{method: 'classified'}])).toBe(
      'Delta % (classified)'
    );
    expect(deltaStudyTitle([{method: 'true'}, {method: 'classified'}])).toBe(
      'Delta % (true; classified)'
    );
    expect(deltaOpacity('true')).toBe(TRUE_DELTA_OPACITY);
  });
});
