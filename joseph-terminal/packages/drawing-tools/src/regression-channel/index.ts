/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Regression channel: the market engine's least-squares line
 * over the bars between two anchors with ±k·σ channel lines. The browser
 * computes nothing; it requests the series and draws the returned values.
 *
 * - Anchors: two, t1 then t2. The earlier anchor (in time) is t1 and the
 *   later is t2, so dragging one past the other keeps the drawing valid.
 *   Each anchor's time picks its bar on the chart timeframe; prices only
 *   place handles until values arrive.
 * - Series: `GET /v1/indicators/series?plugin=regression_channel&from_ms=
 *   <t2 bar start>` with settings `{anchor_ms: <t1 bar start>, source:
 *   'close', deviations}` (decimal string). The point whose `bar_start_ms`
 *   equals the t2 bar carries `start_ms` (int or null), `start`, `end`,
 *   `upper_start`, `upper_end`, `lower_start`, `lower_end`, `slope`,
 *   `stdev`, `pearson_r`, `max_up`, `max_down` (decimal strings or null)
 *   and `bars` (int). The host re-requests when an anchor or input changes
 *   and when a bar closes.
 * - Geometry: centre line from (`start_ms`, `start`) to (t2 bar, `end`),
 *   dashed; upper line from `upper_start` to `upper_end` and lower line
 *   from `lower_start` to `lower_end`; optional fill between them; optional
 *   "Pearson's R" label at the right end.
 * - Status: the engine computes from anchors up to 50 000 bars back; when
 *   it refuses the anchors the notice shows the engine's own message. Both
 *   anchors on one bar, an unclosed t2 bar, null values, loading and failed
 *   requests show a notice too.
 *   Dashed vertical markers show both anchor bars while nothing is drawn.
 * - Hit-testing: handles first, then the three lines, then the inside of
 *   the channel when filled (or the vertical markers when nothing is
 *   drawn).
 * - Handles: two; anchor i sits on its end of the centre line when drawn,
 *   otherwise at the anchor itself.
 * - Style: color, width and dash of the channel lines; `fill_color` fills
 *   the channel (on by default). Inputs: deviations (1-3σ) and the
 *   Pearson's R label.
 */

import {
  Decimal,
  CONTINUOUS_CALENDAR,
  type BarCalendar,
  type DrawingDocument,
  type Timeframe,
} from '@joseph/contracts';
import {
  anchorPoints,
  applyStroke,
  barStartAt,
  decimalValue,
  distanceToSegment,
  drawHandles,
  drawLabel,
  drawNotice,
  formatPrice,
  hitHandles,
  inputDefaults,
  integerValue,
  LABEL_CHAR_WIDTH_PX,
  noticeBounds,
  pointInPolygon,
  pointInRect,
  readInput,
  seriesPointAt,
  seriesStatusMessage,
  strokePath,
  type DrawingSeriesRequest,
  type DrawingSeriesState,
  type PixelTransform,
  type Point,
  type ToolInput,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Engine indicator plugin id of the series request. */
export const REGRESSION_CHANNEL_PLUGIN = 'regression_channel';

/** Price source sent to the engine: bar closes. */
export const REGRESSION_SOURCE = 'close';

/** Notice when both anchors fall on one bar. */
export const SAME_BAR_MESSAGE = 'Place the two anchors on different bars';

/** Tool inputs (props `deviations`, `show_pearson`). */
export const REGRESSION_INPUTS: readonly ToolInput[] = [
  {
    key: 'deviations',
    label: 'Deviations',
    kind: 'choice',
    defaultValue: '2',
    choices: ['1', '1.5', '2', '2.5', '3'].map(value => ({
      value,
      label: `${value}σ`,
    })),
  },
  {
    key: 'show_pearson',
    label: "Pearson's R",
    kind: 'boolean',
    defaultValue: true,
  },
];

/** Engine values of the channel at the t2 bar (prices as decimal strings). */
export interface ChannelValues {
  /** Start of the regression's first bar, epoch milliseconds (UTC). */
  startMs: number;
  /** Start of the t2 bar, epoch milliseconds (UTC). */
  endMs: number;
  /** Centre-line price at the first bar. */
  start: string;
  /** Centre-line price at the t2 bar. */
  end: string;
  /** Upper line at the first bar. */
  upperStart: string;
  /** Upper line at the t2 bar. */
  upperEnd: string;
  /** Lower line at the first bar. */
  lowerStart: string;
  /** Lower line at the t2 bar. */
  lowerEnd: string;
  /** Pearson's R, when published. */
  pearsonR: string | null;
  /** Residual standard deviation in price units, when published. */
  stdev: string | null;
  /** Slope in price units per bar, when published. */
  slope: string | null;
  /** Bars in the regression, when published. */
  bars: number | null;
}

/** What the tool shows for a series state. */
export type ChannelView =
  | {
      /** Lines are drawn. */
      kind: 'ready';
      /** Engine values. */
      values: ChannelValues;
    }
  | {
      /** A notice replaces the lines. */
      kind: 'notice';
      /** Notice text. */
      text: string;
    };

/**
 * Orders the anchors by time.
 * @param doc Drawing.
 * @return `[earlier, later]` anchor indices, or null without two anchors.
 */
export function anchorOrder(
  doc: Pick<DrawingDocument, 'anchors'>
): [number, number] | null {
  const [a, b] = doc.anchors;
  if (!a || !b) return null;
  return a.time_ms <= b.time_ms ? [0, 1] : [1, 0];
}

/**
 * Returns the bar starts of t1 and t2 on a timeframe.
 * @param doc Drawing.
 * @param timeframe Chart timeframe; each anchor time is rounded to the
 *     nearest bar start on it.
 * @param calendar Session calendar of the instrument (default UTC bars).
 * @return `[t1Bar, t2Bar]` in epoch milliseconds, or null without two
 *     anchors.
 */
export function channelBars(
  doc: Pick<DrawingDocument, 'anchors'>,
  timeframe: Timeframe,
  calendar?: BarCalendar
): [number, number] | null {
  const order = anchorOrder(doc);
  const first = order ? doc.anchors[order[0]] : undefined;
  const second = order ? doc.anchors[order[1]] : undefined;
  if (!first || !second) return null;
  return [
    barStartAt(first.time_ms, timeframe, calendar),
    barStartAt(second.time_ms, timeframe, calendar),
  ];
}

/**
 * Builds the engine request for a drawing.
 * @param doc Drawing.
 * @param timeframe Chart timeframe.
 * @param calendar Session calendar of the instrument (default UTC bars).
 * @return Request, or null without two anchors. Anchors on one bar still
 *     request (the tool then shows a notice), so the state stays known.
 */
export function regressionRequest(
  doc: Pick<DrawingDocument, 'anchors' | 'props'>,
  timeframe: Timeframe,
  calendar?: BarCalendar
): DrawingSeriesRequest | null {
  const bars = channelBars(doc, timeframe, calendar);
  if (!bars) return null;
  // The input always exists; the fallback only satisfies index checks.
  const deviations = REGRESSION_INPUTS[0];
  return {
    pluginId: REGRESSION_CHANNEL_PLUGIN,
    fromMs: bars[1],
    settings: {
      anchor_ms: bars[0],
      source: REGRESSION_SOURCE,
      deviations: deviations ? String(readInput(doc, deviations)) : '2',
    },
  };
}

/**
 * Interprets a series state as channel lines or a notice.
 * @param doc Drawing (its anchors decide the same-bar notice).
 * @param series Series state (null before any request).
 * @return Engine values at the t2 bar, or a notice (same bar, loading,
 *     error, refusal or no values yet).
 */
export function channelView(
  doc: Pick<DrawingDocument, 'anchors'>,
  series: DrawingSeriesState | null
): ChannelView {
  // Checked before the series status: whatever the engine answers, the user
  // must first move an anchor.
  if (series) {
    const bars = channelBars(doc, series.timeframe, series.calendar);
    if (bars && bars[0] === bars[1]) {
      return {kind: 'notice', text: SAME_BAR_MESSAGE};
    }
  }
  const message = seriesStatusMessage(series);
  if (!series || message) return {kind: 'notice', text: message ?? ''};
  const endMs = series.request.fromMs;
  const point = seriesPointAt(series.points, endMs);
  if (!point) {
    // The engine publishes the channel on the t2 bar once that bar has closed.
    if (
      series.asOfMs === null ||
      series.asOfMs <=
        (series.calendar ?? CONTINUOUS_CALENDAR).endOf(series.timeframe, endMs)
    ) {
      return {kind: 'notice', text: 'Waiting for the end bar to close'};
    }
    return {kind: 'notice', text: 'No engine values for these anchors yet'};
  }
  const startMs = integerValue(point, 'start_ms');
  const keys = [
    'start',
    'end',
    'upper_start',
    'upper_end',
    'lower_start',
    'lower_end',
  ] as const;
  const [start, end, upperStart, upperEnd, lowerStart, lowerEnd] = keys.map(
    key => decimalValue(point, key)
  );
  if (
    startMs === null ||
    start == null ||
    end == null ||
    upperStart == null ||
    upperEnd == null ||
    lowerStart == null ||
    lowerEnd == null
  ) {
    return {kind: 'notice', text: 'No engine values for these anchors yet'};
  }
  return {
    kind: 'ready',
    values: {
      startMs,
      endMs,
      start,
      end,
      upperStart,
      upperEnd,
      lowerStart,
      lowerEnd,
      pearsonR: decimalValue(point, 'pearson_r'),
      stdev: decimalValue(point, 'stdev'),
      slope: decimalValue(point, 'slope'),
      bars: integerValue(point, 'bars'),
    },
  };
}

/** Channel lines in CSS pixels, each from the first bar to the t2 bar. */
interface ChannelPixels {
  /** Centre line. */
  centre: [Point, Point];
  /** Upper line. */
  upper: [Point, Point];
  /** Lower line. */
  lower: [Point, Point];
}

/**
 * Projects the channel's line ends to CSS pixels.
 * @param transform Current chart transform.
 * @param values Engine values.
 * @return Pixel lines, or null when an end is off the time or price scale.
 */
function channelPixels(
  transform: PixelTransform,
  values: ChannelValues
): ChannelPixels | null {
  const x1 = transform.timeToX(values.startMs);
  const x2 = transform.timeToX(values.endMs);
  if (x1 === null || x2 === null) return null;
  const at = (x: number, price: string): Point | null => {
    const y = transform.priceToY(Number(price));
    return y === null ? null : {x, y};
  };
  const ends = [
    at(x1, values.start),
    at(x2, values.end),
    at(x1, values.upperStart),
    at(x2, values.upperEnd),
    at(x1, values.lowerStart),
    at(x2, values.lowerEnd),
  ];
  const [c1, c2, u1, u2, l1, l2] = ends;
  if (!c1 || !c2 || !u1 || !u2 || !l1 || !l2) return null;
  return {centre: [c1, c2], upper: [u1, u2], lower: [l1, l2]};
}

/**
 * Returns the handle positions, indexed like the anchors.
 * They sit on the centre line's ends when drawn, else at the anchors.
 * @param doc Drawing.
 * @param transform Current chart transform.
 * @param lines Channel pixels when drawn, else null.
 * @return Handles in CSS pixels; empty when the anchors cannot be placed.
 */
function handlesOf(
  doc: DrawingDocument,
  transform: PixelTransform,
  lines: ChannelPixels | null
): Point[] {
  const anchors = anchorPoints(transform, doc) ?? [];
  const order = anchorOrder(doc);
  if (!lines || !order || anchors.length < 2) return anchors;
  const handles = [...anchors];
  handles[order[0]] = lines.centre[0];
  handles[order[1]] = lines.centre[1];
  return handles;
}

/**
 * Returns the x positions of the dashed anchor-bar markers.
 * @param doc Drawing.
 * @param transform Current chart transform.
 * @param series Series state; null marks the raw anchor times instead of
 *     their bars.
 * @return X positions in CSS pixels; anchors off the time scale are left
 *     out.
 */
function markerXs(
  doc: DrawingDocument,
  transform: PixelTransform,
  series: DrawingSeriesState | null
): number[] {
  const bars = series
    ? channelBars(doc, series.timeframe, series.calendar)
    : doc.anchors.map(a => a.time_ms);
  return (bars ?? [])
    .map(time => transform.timeToX(time))
    .filter((x): x is number => x !== null);
}

/**
 * Returns where the notice text starts, beside the left-most handle.
 * It starts 8 px right of it, level with it and kept 16 px inside the plot.
 * @param transform Current chart transform (plot height).
 * @param handles Handle positions (anchors while nothing is drawn).
 * @return Text origin in CSS pixels, or null without anchors.
 */
function noticeAt(
  transform: PixelTransform,
  handles: readonly Point[]
): Point | null {
  const [a, b] = handles;
  if (!a) return null;
  const first = b && b.x < a.x ? b : a;
  return {
    x: first.x + 8,
    y: Math.max(16, Math.min(transform.height - 16, first.y)),
  };
}

/** Regression channel tool (engine-backed). */
export const regressionChannelTool: ToolPlugin = {
  manifest: {
    id: 'regression-channel',
    version: 1,
    displayName: 'Regression channel',
    group: 'studies',
    icon: 'chart-scatter',
    anchorCount: 2,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    // A hex alpha byte on the `#rrggbb` user color: 0x14, about 8 % opacity.
    fill_color: `${palette.user}14`,
  }),
  defaultProps: () => inputDefaults(REGRESSION_INPUTS),
  inputs: REGRESSION_INPUTS,
  seriesRequest: (doc, env) =>
    regressionRequest(doc, env.timeframe, env.calendar),
  render(ctx, doc, transform, state) {
    const series = state.series ?? null;
    const view = channelView(doc, series);
    const lines =
      view.kind === 'ready' ? channelPixels(transform, view.values) : null;
    const handles = handlesOf(doc, transform, lines);
    if (view.kind === 'notice' || !lines) {
      applyStroke(ctx, doc.style, state);
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      for (const x of markerXs(doc, transform, series)) {
        strokePath(ctx, [
          {x, y: 0},
          {x, y: transform.height},
        ]);
      }
      const [a, b] = handles;
      if (a && b) strokePath(ctx, [a, b]);
      const text = view.kind === 'notice' ? view.text : '';
      const at = noticeAt(transform, handles);
      if (text && !state.preview && at) {
        drawNotice(ctx, text, at.x, at.y, state, transform.width);
      }
      drawHandles(ctx, handles, state);
      return;
    }
    if (doc.style.fill_color) {
      ctx.fillStyle = doc.style.fill_color;
      ctx.beginPath();
      ctx.moveTo(lines.upper[0].x, lines.upper[0].y);
      ctx.lineTo(lines.upper[1].x, lines.upper[1].y);
      ctx.lineTo(lines.lower[1].x, lines.lower[1].y);
      ctx.lineTo(lines.lower[0].x, lines.lower[0].y);
      ctx.closePath();
      ctx.fill();
    }
    applyStroke(ctx, doc.style, state);
    strokePath(ctx, lines.upper);
    strokePath(ctx, lines.lower);
    ctx.setLineDash([6, 4]);
    strokePath(ctx, lines.centre);
    // The input always exists; the fallback only satisfies index checks.
    const showPearson = REGRESSION_INPUTS[1]
      ? readInput(doc, REGRESSION_INPUTS[1]) === true
      : false;
    if (showPearson && view.values.pearsonR !== null) {
      // Keeps the label (about 110 px wide) inside the plot's right edge.
      drawLabel(
        ctx,
        `Pearson's R ${Decimal.parse(view.values.pearsonR).toFixed(3)}`,
        Math.min(lines.lower[1].x + 6, transform.width - 110),
        lines.lower[1].y + 12,
        doc.style.color,
        state
      );
    }
    drawHandles(ctx, handles, state);
  },
  hitTest(doc, transform, point, tolerance, series) {
    const view = channelView(doc, series ?? null);
    const lines =
      view.kind === 'ready' ? channelPixels(transform, view.values) : null;
    const handle = hitHandles(
      handlesOf(doc, transform, lines),
      point,
      tolerance
    );
    if (handle) return handle;
    if (!lines) {
      const text = view.kind === 'notice' ? view.text : '';
      const at = noticeAt(transform, handlesOf(doc, transform, null));
      const onPlate =
        text !== '' &&
        at !== null &&
        pointInRect(
          point,
          noticeBounds(
            text.length * LABEL_CHAR_WIDTH_PX,
            at.x,
            at.y,
            transform.width
          )
        );
      return onPlate ||
        markerXs(doc, transform, series ?? null).some(
          x => Math.abs(point.x - x) <= tolerance
        )
        ? {part: 'body'}
        : null;
    }
    const near = [lines.centre, lines.upper, lines.lower].some(
      ([a, b]) => distanceToSegment(point, a, b) <= tolerance
    );
    const inside =
      Boolean(doc.style.fill_color) &&
      pointInPolygon(point, [
        lines.upper[0],
        lines.upper[1],
        lines.lower[1],
        lines.lower[0],
      ]);
    return near || inside ? {part: 'body'} : null;
  },
  dataRows(doc, _barStartMs, {series, tickSize}) {
    const view = channelView(doc, series);
    if (view.kind === 'notice') {
      return series?.refusal
        ? [{label: 'Regression', value: 'refused by the engine'}]
        : [];
    }
    const {values} = view;
    const rows = [
      {label: 'Regression end', value: formatPrice(values.end, tickSize)},
      {label: 'Channel upper', value: formatPrice(values.upperEnd, tickSize)},
      {label: 'Channel lower', value: formatPrice(values.lowerEnd, tickSize)},
    ];
    if (values.pearsonR !== null) {
      rows.push({
        label: "Pearson's R",
        value: Decimal.parse(values.pearsonR).toFixed(3),
      });
    }
    return rows;
  },
  describe(doc, context) {
    if (!context?.series) return 'Regression channel (engine)';
    const view = channelView(doc, context.series);
    if (view.kind === 'notice') return view.text;
    const {values} = view;
    const parts = [
      values.pearsonR === null
        ? null
        : `R ${Decimal.parse(values.pearsonR).toFixed(3)}`,
      `end ${formatPrice(values.end, context.tickSize)}`,
      values.bars === null ? null : `${values.bars} bars`,
    ].filter((part): part is string => part !== null);
    return parts.join(' · ');
  },
};
