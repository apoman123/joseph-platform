/**
 * @fileoverview Heikin Ashi display transform.
 *
 * Heikin Ashi bars are synthetic smoothed prices. They are a rendering
 * option only and must never feed signals, risk or order prices; the chart
 * keeps the raw bars for every coordinate that can reach an order.
 */
import type {ChartBar} from '../chart-port';

/**
 * Computes one Heikin Ashi bar from a raw bar and the previous HA bar.
 * @param bar Raw bar.
 * @param previous Previous Heikin Ashi bar, or null for the first bar.
 * @return Heikin Ashi bar with the raw volume and finality.
 */
export function heikinAshiBar(
  bar: ChartBar,
  previous: ChartBar | null
): ChartBar {
  const close = (bar.open + bar.high + bar.low + bar.close) / 4;
  const open = previous
    ? (previous.open + previous.close) / 2
    : (bar.open + bar.close) / 2;
  return {
    timeMs: bar.timeMs,
    open,
    close,
    high: Math.max(bar.high, open, close),
    low: Math.min(bar.low, open, close),
    volume: bar.volume,
    final: bar.final,
  };
}

/**
 * Transforms a sorted bar series into Heikin Ashi bars.
 * @param bars Raw bars sorted by time.
 * @return Heikin Ashi bars of equal length.
 */
export function toHeikinAshi(bars: readonly ChartBar[]): ChartBar[] {
  const result: ChartBar[] = [];
  let previous: ChartBar | null = null;
  for (const bar of bars) {
    previous = heikinAshiBar(bar, previous);
    result.push(previous);
  }
  return result;
}
