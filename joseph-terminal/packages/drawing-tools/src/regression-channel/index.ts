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

/** Engine plugin id. */
export const REGRESSION_CHANNEL_PLUGIN = 'regression_channel';

/** Price source sent to the engine. */
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

/** Engine values of the channel at the t2 bar (decimal strings). */
export interface ChannelValues {
  /** First bar of the regression (UTC ms). */
  startMs: number;
  /** t2 bar (UTC ms). */
  endMs: number;
  /** Centre at the first bar. */
  start: string;
  /** Centre at the t2 bar. */
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
  /** Residual standard deviation, when published. */
  stdev: string | null;
  /** Slope per bar, when published. */
  slope: string | null;
  /** Bars in the regression. */
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
 * Bar starts of t1 and t2 on a timeframe.
 * @param doc Drawing.
 * @param timeframe Chart timeframe.
 * @param calendar Session calendar of the instrument (default UTC bars).
 * @return `[t1Bar, t2Bar]` or null.
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
 * Interprets a series state.
 * @param doc Drawing.
 * @param series Series state (null before any request).
 * @return Lines to draw or a notice.
 */
export function channelView(
  doc: Pick<DrawingDocument, 'anchors'>,
  series: DrawingSeriesState | null
): ChannelView {
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

/** Channel lines in pixels. */
interface ChannelPixels {
  /** Centre line. */
  centre: [Point, Point];
  /** Upper line. */
  upper: [Point, Point];
  /** Lower line. */
  lower: [Point, Point];
}

/**
 * Projects the channel to pixels.
 * @param transform Transform.
 * @param values Engine values.
 * @return Pixel lines, or null when off the time scale.
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
 * Handle positions (index = anchor index).
 * @param doc Drawing.
 * @param transform Transform.
 * @param lines Channel pixels when drawn.
 * @return Handles.
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
 * X positions of the anchor bar markers.
 * @param doc Drawing.
 * @param transform Transform.
 * @param series Series state.
 * @return X positions.
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
 * Where the notice text starts: right of the earlier anchor, beside it
 * vertically and inside the plot.
 * @param transform Transform.
 * @param handles Handle positions (anchors while nothing is drawn).
 * @return Text origin, or null without anchors.
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
    const showPearson = REGRESSION_INPUTS[1]
      ? readInput(doc, REGRESSION_INPUTS[1]) === true
      : false;
    if (showPearson && view.values.pearsonR !== null) {
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
