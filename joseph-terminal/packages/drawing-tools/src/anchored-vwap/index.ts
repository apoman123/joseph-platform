/**
 * @fileoverview Anchored VWAP: the market engine's volume-weighted average
 * price from one anchor bar forward, with optional standard-deviation
 * bands. The browser computes nothing; it requests the series and draws it.
 *
 * - Anchors: one. Its time picks the anchor bar (the bar nearest to it on
 *   the chart timeframe); its price only places the handle until values
 *   arrive.
 * - Series: `GET /v1/indicators/series?plugin=anchored_vwap&from_ms=<anchor
 *   bar start>` with settings `{anchor: 'time', anchor_ms: <anchor bar
 *   start>, source: 'hlc3', band_1, band_2, band_3}` (decimal strings;
 *   `'0'` turns a band off). Each point carries `vwap`, `stdev`,
 *   `upper_k`/`lower_k` (decimal strings or null), `anchor_ms` (int or
 *   null) and `bars` (int). The host re-requests when the anchor or an
 *   input changes and when a bar closes.
 * - Geometry: a polyline through `(bar_start_ms, vwap)` of every bar from
 *   the anchor bar on; for each enabled band, polylines through `upper_k`
 *   and `lower_k`; optional fill between the band 1 lines.
 * - Status: while loading or after a failed request a notice replaces the
 *   lines. The engine computes from anchors up to 50 000 bars back; when it
 *   refuses an anchor the notice shows the engine's own message. A dashed
 *   vertical marker shows the anchor bar in every non-drawn state so it can
 *   be dragged.
 * - Hit-testing: the handle first, then the VWAP and band polylines (or
 *   the vertical marker when nothing is drawn).
 * - Handles: one, at the VWAP of the anchor bar when known, otherwise at
 *   the anchor itself. Dragging it moves the anchor and re-requests.
 * - Style: color, width and dash of the VWAP line (bands use the same
 *   color, 1 px, dashed); `fill_color` fills band 1 (off by default);
 *   `show_labels` tags the latest value. Inputs: band 1-3 multipliers.
 */
import {
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
  distanceToPolyline,
  drawHandles,
  drawLabel,
  drawNotice,
  formatPrice,
  hitHandles,
  inputDefaults,
  LABEL_CHAR_WIDTH_PX,
  noticeBounds,
  pointInRect,
  readInput,
  seriesPointAt,
  seriesStatusMessage,
  strokePath,
  type DrawingSeriesRequest,
  type DrawingSeriesState,
  type PixelTransform,
  type Point,
  type ToolChoiceInput,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Engine plugin id. */
export const ANCHORED_VWAP_PLUGIN = 'anchored_vwap';

/** Price source sent to the engine. */
export const ANCHORED_VWAP_SOURCE = 'hlc3';

/** Band multiplier choices (`'0'` = off). */
const BAND_CHOICES: ToolChoiceInput['choices'] = [
  {value: '0', label: 'Off'},
  {value: '0.5', label: '0.5σ'},
  {value: '1', label: '1σ'},
  {value: '1.5', label: '1.5σ'},
  {value: '2', label: '2σ'},
  {value: '2.5', label: '2.5σ'},
  {value: '3', label: '3σ'},
];

/** Band inputs (props `band_1` .. `band_3`). */
export const ANCHORED_VWAP_INPUTS: readonly ToolChoiceInput[] = [1, 2, 3].map(
  (k): ToolChoiceInput => ({
    key: `band_${k}`,
    label: `Band ${k}`,
    kind: 'choice',
    defaultValue: k === 1 ? '1' : '0',
    choices: BAND_CHOICES,
  })
);

/** One plotted value. */
export interface PlotPoint {
  /** Bar start (UTC ms). */
  timeMs: number;
  /** Value as a display number. */
  value: number;
}

/** One enabled band. */
export interface VwapBand {
  /** Band number (1-3). */
  k: number;
  /** Multiplier (decimal string). */
  multiplier: string;
  /** Upper line. */
  upper: PlotPoint[];
  /** Lower line. */
  lower: PlotPoint[];
}

/** What the tool shows for a series state. */
export type VwapView =
  | {
      /** Lines are drawn. */
      kind: 'ready';
      /** Anchor bar start. */
      anchorBarMs: number;
      /** VWAP line. */
      line: PlotPoint[];
      /** Enabled bands. */
      bands: VwapBand[];
      /** Latest VWAP (decimal string). */
      latest: string;
    }
  | {
      /** A notice replaces the lines. */
      kind: 'notice';
      /** Anchor bar start, or null before any request. */
      anchorBarMs: number | null;
      /** Notice text. */
      text: string;
    };

/**
 * Builds the engine request for a drawing.
 * @param doc Drawing.
 * @param timeframe Chart timeframe.
 * @param calendar Session calendar of the instrument (default UTC bars).
 * @return Request, or null without an anchor.
 */
export function anchoredVwapRequest(
  doc: Pick<DrawingDocument, 'anchors' | 'props'>,
  timeframe: Timeframe,
  calendar?: BarCalendar
): DrawingSeriesRequest | null {
  const anchor = doc.anchors[0];
  if (!anchor) return null;
  const anchorMs = barStartAt(anchor.time_ms, timeframe, calendar);
  const bands = Object.fromEntries(
    ANCHORED_VWAP_INPUTS.map(input => [
      input.key,
      String(readInput(doc, input)),
    ])
  );
  return {
    pluginId: ANCHORED_VWAP_PLUGIN,
    fromMs: anchorMs,
    settings: {
      anchor: 'time',
      anchor_ms: anchorMs,
      source: ANCHORED_VWAP_SOURCE,
      ...bands,
    },
  };
}

/**
 * Collects a value line from series points.
 * @param points Points.
 * @param key Value key.
 * @return Plot points (nulls skipped).
 */
function lineOf(
  points: DrawingSeriesState['points'],
  key: string
): PlotPoint[] {
  const line: PlotPoint[] = [];
  for (const point of points) {
    const value = decimalValue(point, key);
    if (value !== null)
      line.push({timeMs: point.bar_start_ms, value: Number(value)});
  }
  return line;
}

/**
 * Interprets a series state.
 * @param series Series state (null before any request).
 * @return View: lines to draw, or a notice.
 */
export function vwapView(series: DrawingSeriesState | null): VwapView {
  const message = seriesStatusMessage(series);
  if (!series || message) {
    return {
      kind: 'notice',
      anchorBarMs: series?.request.fromMs ?? null,
      text: message ?? '',
    };
  }
  const anchorBarMs = series.request.fromMs;
  const points = series.points.filter(p => p.bar_start_ms >= anchorBarMs);
  const line = lineOf(points, 'vwap');
  const lastPoint = [...points]
    .reverse()
    .find(p => decimalValue(p, 'vwap') !== null);
  const latest = decimalValue(lastPoint, 'vwap');
  if (line.length === 0 || latest === null) {
    const pending =
      points.length === 0 &&
      (series.asOfMs === null ||
        series.asOfMs <=
          (series.calendar ?? CONTINUOUS_CALENDAR).endOf(
            series.timeframe,
            anchorBarMs
          ));
    return {
      kind: 'notice',
      anchorBarMs,
      text: pending
        ? 'Waiting for the anchor bar to close'
        : 'No engine values for this anchor yet',
    };
  }
  const bands: VwapBand[] = [];
  for (let k = 1; k <= 3; k += 1) {
    const multiplier = series.request.settings[`band_${k}`];
    if (typeof multiplier !== 'string' || Number(multiplier) <= 0) continue;
    bands.push({
      k,
      multiplier,
      upper: lineOf(points, `upper_${k}`),
      lower: lineOf(points, `lower_${k}`),
    });
  }
  return {kind: 'ready', anchorBarMs, line, bands, latest};
}

/**
 * Projects a value line to pixels, skipping points off the time scale.
 * @param transform Transform.
 * @param line Plot points.
 * @return Pixels.
 */
function pixels(
  transform: PixelTransform,
  line: readonly PlotPoint[]
): Point[] {
  const out: Point[] = [];
  for (const {timeMs, value} of line) {
    const x = transform.timeToX(timeMs);
    const y = transform.priceToY(value);
    if (x !== null && y !== null) out.push({x, y});
  }
  return out;
}

/**
 * Handle position: the VWAP at the anchor bar when drawn, else the anchor.
 * @param doc Drawing.
 * @param transform Transform.
 * @param view View.
 * @return Handle or null.
 */
function handleOf(
  doc: DrawingDocument,
  transform: PixelTransform,
  view: VwapView
): Point | null {
  const first = view.kind === 'ready' ? view.line[0] : undefined;
  if (first) {
    const [point] = pixels(transform, [first]);
    if (point) return point;
  }
  return anchorPoints(transform, doc)?.[0] ?? null;
}

/**
 * X of the anchor marker.
 * @param doc Drawing.
 * @param transform Transform.
 * @param view View.
 * @return X or null.
 */
function markerX(
  doc: DrawingDocument,
  transform: PixelTransform,
  view: VwapView
): number | null {
  const time = view.anchorBarMs ?? doc.anchors[0]?.time_ms;
  return time === undefined ? null : transform.timeToX(time);
}

/**
 * Vertical centre of the notice: beside the handle, kept inside the plot.
 * @param transform Transform.
 * @param handle Handle position.
 * @return Y.
 */
function noticeY(transform: PixelTransform, handle: Point): number {
  return Math.max(16, Math.min(transform.height - 16, handle.y));
}

/** Anchored VWAP tool (engine-backed). */
export const anchoredVwapTool: ToolPlugin = {
  manifest: {
    id: 'anchored-vwap',
    version: 1,
    displayName: 'Anchored VWAP',
    group: 'studies',
    icon: 'anchor',
    anchorCount: 1,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 2,
    line_style: 'solid',
    fill_color: null,
    show_labels: true,
  }),
  defaultProps: () => inputDefaults(ANCHORED_VWAP_INPUTS),
  inputs: ANCHORED_VWAP_INPUTS,
  seriesRequest: (doc, env) =>
    anchoredVwapRequest(doc, env.timeframe, env.calendar),
  render(ctx, doc, transform, state) {
    const view = vwapView(state.series ?? null);
    const handle = handleOf(doc, transform, view);
    if (!handle) return;
    if (view.kind === 'notice') {
      const x = markerX(doc, transform, view) ?? handle.x;
      applyStroke(ctx, doc.style, state);
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      strokePath(ctx, [
        {x, y: 0},
        {x, y: transform.height},
      ]);
      if (view.text) {
        drawNotice(
          ctx,
          view.text,
          x + 8,
          noticeY(transform, handle),
          state,
          transform.width
        );
      }
      drawHandles(ctx, [handle], state);
      return;
    }
    const band1 = view.bands.find(band => band.k === 1);
    if (doc.style.fill_color && band1) {
      const upper = pixels(transform, band1.upper);
      const lower = pixels(transform, band1.lower).reverse();
      if (upper.length > 1 && lower.length > 1) {
        ctx.fillStyle = doc.style.fill_color;
        ctx.beginPath();
        [...upper, ...lower].forEach((p, i) =>
          i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)
        );
        ctx.closePath();
        ctx.fill();
      }
    }
    for (const band of view.bands) {
      applyStroke(ctx, doc.style, state);
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      strokePath(ctx, pixels(transform, band.upper));
      strokePath(ctx, pixels(transform, band.lower));
    }
    applyStroke(ctx, doc.style, state);
    const line = pixels(transform, view.line);
    strokePath(ctx, line);
    const last = line.at(-1);
    if (last && doc.style.show_labels !== false) {
      drawLabel(
        ctx,
        `VWAP ${formatPrice(view.latest, state.tickSize)}`,
        Math.min(last.x + 6, transform.width - 90),
        last.y,
        doc.style.color,
        state
      );
    }
    drawHandles(ctx, [handle], state);
  },
  hitTest(doc, transform, point, tolerance, series) {
    const view = vwapView(series ?? null);
    const handle = handleOf(doc, transform, view);
    if (!handle) return null;
    const hit = hitHandles([handle], point, tolerance);
    if (hit) return hit;
    if (view.kind === 'notice') {
      const x = markerX(doc, transform, view) ?? handle.x;
      const plate = noticeBounds(
        view.text.length * LABEL_CHAR_WIDTH_PX,
        x + 8,
        noticeY(transform, handle),
        transform.width
      );
      return Math.abs(point.x - x) <= tolerance ||
        (view.text !== '' && pointInRect(point, plate))
        ? {part: 'body'}
        : null;
    }
    const lines = [
      view.line,
      ...view.bands.flatMap(band => [band.upper, band.lower]),
    ];
    return lines.some(
      line => distanceToPolyline(point, pixels(transform, line)) <= tolerance
    )
      ? {part: 'body'}
      : null;
  },
  dataRows(_doc, barStartMs, {series, tickSize}) {
    const view = vwapView(series);
    if (view.kind === 'notice') {
      return series?.refusal
        ? [{label: 'Anchored VWAP', value: 'refused by the engine'}]
        : [];
    }
    const point = seriesPointAt(series?.points ?? [], barStartMs);
    const vwap = decimalValue(point, 'vwap');
    if (vwap === null) return [];
    const rows = [{label: 'Anchored VWAP', value: formatPrice(vwap, tickSize)}];
    for (const band of view.bands) {
      const upper = decimalValue(point, `upper_${band.k}`);
      const lower = decimalValue(point, `lower_${band.k}`);
      if (upper !== null)
        rows.push({
          label: `VWAP +${band.multiplier}σ`,
          value: formatPrice(upper, tickSize),
        });
      if (lower !== null)
        rows.push({
          label: `VWAP −${band.multiplier}σ`,
          value: formatPrice(lower, tickSize),
        });
    }
    return rows;
  },
  describe(doc, context) {
    const view = vwapView(context?.series ?? null);
    if (!context?.series) return 'Anchored VWAP (engine)';
    if (view.kind === 'notice') return view.text;
    const from = new Date(view.anchorBarMs)
      .toISOString()
      .slice(0, 16)
      .replace('T', ' ');
    const bands = view.bands.map(band => `${band.multiplier}σ`).join(', ');
    return `VWAP ${formatPrice(view.latest, context.tickSize)} from ${from} UTC${bands ? ` · bands ${bands}` : ''}`;
  },
};
