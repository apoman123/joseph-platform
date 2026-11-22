/**
 * @fileoverview Price/time measurement between two anchors.
 */
import {Decimal} from '@joseph/contracts';
import {
  anchorPoints,
  applyStroke,
  drawHandles,
  drawLabel,
  formatPrice,
  hitHandles,
  pointInRect,
  rectFromPoints,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Measurement result. */
export interface Measurement {
  /** Signed price change. */
  delta: string;
  /** Signed percent change (2 decimals). */
  pct: string;
  /** Number of bars spanned. */
  bars: number;
  /** Human-readable duration. */
  duration: string;
}

/**
 * Measures the span between two anchors.
 * @param from Start anchor.
 * @param to End anchor.
 * @param durationMs Bar duration.
 * @return Measurement.
 */
export function measure(
  from: {time_ms: number; price: string},
  to: {time_ms: number; price: string},
  durationMs: number
): Measurement {
  const start = Decimal.parse(from.price);
  const delta = Decimal.parse(to.price).sub(start);
  const pct = start.isZero()
    ? '0.00'
    : delta.div(start).mul(Decimal.parse('100')).toFixed(2);
  const spanMs = Math.abs(to.time_ms - from.time_ms);
  const minutes = Math.round(spanMs / 60_000);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  const duration = days > 0 ? `${days}d ${hours}h` : `${hours}h ${mins}m`;
  return {
    delta: delta.toString(),
    pct,
    bars: Math.round(spanMs / Math.max(1, durationMs)),
    duration,
  };
}

/** Price range (ruler) tool. */
export const priceRangeTool: ToolPlugin = {
  manifest: {
    id: 'price-range',
    version: 1,
    displayName: 'Measure',
    group: 'measure',
    icon: 'ruler',
    anchorCount: 2,
    hotkey: 'alt+m',
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'dashed',
  }),
  render(ctx, doc, transform, state) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    const [from, to] = doc.anchors;
    if (!a || !b || !from || !to) return;
    const rect = rectFromPoints(a, b);
    const m = measure(from, to, state.durationMs);
    const up = !m.delta.startsWith('-');
    const tone = up ? state.palette.bid : state.palette.ask;
    ctx.fillStyle = `${tone}22`;
    ctx.fillRect(rect.x, rect.y, rect.width, rect.height);
    applyStroke(ctx, doc.style, state);
    ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);
    const sign = up ? '+' : '';
    drawLabel(
      ctx,
      `${sign}${formatPrice(m.delta, state.tickSize)} (${sign}${m.pct}%)`,
      rect.x + 4,
      rect.y - 12,
      tone,
      state
    );
    drawLabel(
      ctx,
      `${m.bars} bars, ${m.duration}`,
      rect.x + 4,
      rect.y + rect.height + 12,
      state.palette.text,
      state
    );
    drawHandles(ctx, [a, b], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [a, b] = anchorPoints(transform, doc) ?? [];
    if (!a || !b) return null;
    return (
      hitHandles([a, b], point, tolerance) ??
      (pointInRect(point, rectFromPoints(a, b), tolerance)
        ? {part: 'body'}
        : null)
    );
  },
  describe: doc =>
    `Measure ${doc.anchors[0]?.price ?? '?'} to ${doc.anchors[1]?.price ?? '?'}`,
};
