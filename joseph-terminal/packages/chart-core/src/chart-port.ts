/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview `ChartPort`: Joseph's own chart abstraction.
 *
 * Only `src/adapters/lwc/`, the Lightweight Charts (LWC) adapter, may import
 * `lightweight-charts` (dependency-cruiser enforces it). Everything else
 * (features, drawing tools, indicator visualization) talks to this port, so
 * the renderer can be replaced without touching strategy logic or contracts.
 *
 * What crosses the port:
 *
 * - Times are UTC epoch milliseconds. The adapter converts them to the
 *   library's UTC seconds and to logical bar indices internally; neither
 *   leaves the adapter.
 * - Pixel coordinates are CSS pixels from the top-left corner of the price
 *   pane's plot area (right of the left comparison axis when it is shown).
 * - Prices in bars, events and transforms are display numbers (floats).
 *   Overlay models carry prices as contract decimal strings instead. A
 *   number read back from the chart (a pointer or drag price) must be
 *   rounded to the tick size and turned into a decimal string before it
 *   can become an order parameter.
 */

import type {
  BarCalendar,
  ChartType,
  PriceScaleMode,
  Timeframe,
} from '@joseph/contracts';
import type {
  OverlayLayer,
  OverlayLineStyle,
  OverlayTone,
} from './overlay-model';

/** Removes a registration (listener, layer, primitive). */
export type Disposer = () => void;

/**
 * Bar shaped for rendering only. Its prices are display numbers and must
 * never be fed back into order parameters.
 */
export interface ChartBar {
  /** Bar start (UTC ms). */
  timeMs: number;
  /** Opening price. */
  open: number;
  /** Highest price. */
  high: number;
  /** Lowest price. */
  low: number;
  /** Closing price. */
  close: number;
  /** Base volume. */
  volume: number;
  /** Whether the bar is final (closed). */
  final: boolean;
}

/** Series identity and presentation. */
export interface SeriesConfig {
  /** Canonical instrument id. */
  instrumentId: string;
  /** Bar resolution. */
  timeframe: Timeframe;
  /** Rendering style. Heikin Ashi is a display transform only. */
  chartType: ChartType;
  /** Price axis mode. */
  priceScale: PriceScaleMode;
  /** Tick size (decimal string); sets the axis precision and minimum move. */
  tickSize: string;
  /** Whether the volume pane is shown. */
  showVolume: boolean;
  /**
   * Session calendar of the instrument. Exchange calendars label the time
   * axis in exchange time and count off-chart time (future anchors, the
   * right margin) in session bars; `24x7` (the default) keeps UTC bars and
   * labels.
   */
  calendar?: BarCalendar;
  /** Unit appended to axis prices, e.g. `%` for yields (none by default). */
  priceUnit?: string;
}

/** Theme colors resolved from CSS design tokens. */
export interface ChartTheme {
  /** Canvas background. */
  background: string;
  /** Grid lines. */
  grid: string;
  /** Axis text. */
  text: string;
  /** Axis borders. */
  border: string;
  /** Rising bars and the `bid` tone. */
  bid: string;
  /** Falling bars and the `ask` and `danger` tones. */
  ask: string;
  /** User-authored marks. */
  user: string;
  /** Crosshair. */
  crosshair: string;
  /** Font family. */
  fontFamily: string;
  /** Warning/polled-data accent (additive, optional). */
  warning?: string;
  /** Replay provenance accent (additive, optional). */
  replay?: string;
  /** De-emphasized marks such as invalidated zones (additive, optional). */
  muted?: string;
}

/** Visible time range. */
export interface TimeRange {
  /** Inclusive start (UTC ms). */
  fromMs: number;
  /** Inclusive end (UTC ms). */
  toMs: number;
}

/** Pointer location in both pixel and data space. */
export interface ChartPoint {
  /** X in CSS pixels from the left edge of the plot area. */
  x: number;
  /** Y in CSS pixels from the top of the price pane. */
  y: number;
  /**
   * Time under the pointer (UTC ms), or null when there are no bars. The
   * LWC adapter reports the start of the bar slot under the pointer.
   */
  timeMs: number | null;
  /** Price under the pointer (display number); null outside the price pane. */
  price: number | null;
}

/** Pointer event emitted by the chart. */
export interface ChartPointerEvent extends ChartPoint {
  /** Mouse button (0 primary, 2 secondary). */
  button: number;
  /** Shift key pressed. */
  shiftKey: boolean;
  /** Ctrl/Meta key pressed. */
  modKey: boolean;
  /**
   * Prevents the browser default and locks chart pan/zoom until the pointer
   * is released, so a tool can own the gesture.
   */
  preventDefault(): void;
}

/** Overlay drag completion. */
export interface OverlayDragEvent {
  /** Layer that owns the model. */
  layerId: string;
  /** Model id. */
  modelId: string;
  /**
   * Price where the drag ended. A display number: round it to the tick size
   * and convert it to a decimal string before any order uses it.
   */
  price: number;
}

/** Event payloads keyed by event name. */
export interface ChartEventMap {
  /** Crosshair moved (null when the pointer leaves the plot). */
  crosshairMove: ChartPoint | null;
  /** Primary-button press and release without moving more than a few pixels. */
  click: ChartPointerEvent;
  /** Secondary click / context menu request. */
  contextMenu: ChartPointerEvent;
  /** Pointer pressed. */
  pointerDown: ChartPointerEvent;
  /** Pointer moved. */
  pointerMove: ChartPointerEvent;
  /** Pointer released. */
  pointerUp: ChartPointerEvent;
  /** Visible range changed (pan, zoom or code); null when there are no bars. */
  visibleRangeChange: TimeRange | null;
  /** Draggable overlay released. */
  overlayDragEnd: OverlayDragEvent;
  /** Container size changed (CSS pixels). */
  resize: {width: number; height: number};
}

/** Event names. */
export type ChartEventName = keyof ChartEventMap;

/** Pixel/data transforms valid for the current frame. */
export interface CoordinateTransform {
  /**
   * Converts a time to an x coordinate.
   * @param timeMs UTC ms (bar start or arbitrary time).
   * @return X in CSS pixels, which may lie outside `0..width` (times beyond
   *     the data are extrapolated), or null when there are no bars.
   */
  timeToX(timeMs: number): number | null;
  /**
   * Converts a price to a y coordinate in the price pane.
   * @param price Display price.
   * @return Y in CSS pixels, or null while the price scale has no data.
   */
  priceToY(price: number): number | null;
  /**
   * Converts an x coordinate to a time.
   * @param x X in CSS pixels.
   * @return Time (UTC ms), or null when there are no bars.
   */
  xToTime(x: number): number | null;
  /**
   * Converts a y coordinate to a price.
   * @param y Y in CSS pixels.
   * @return Display price, or null outside the price pane or without data.
   */
  yToPrice(y: number): number | null;
  /** Plot width in CSS pixels (price axes excluded). */
  readonly width: number;
  /** Price pane height in CSS pixels (volume and study panes excluded). */
  readonly height: number;
}

/**
 * Custom canvas layer used by drawing tools. `draw` is called on every
 * frame with fresh transforms, so anchors stay correct across zoom/resize.
 */
export interface CanvasLayer {
  /** Unique id. */
  readonly id: string;
  /**
   * Paint order among canvas layers; higher draws on top. All canvas layers
   * draw above overlay boxes and rays.
   */
  readonly zIndex: number;
  /**
   * Paints the layer.
   * @param ctx 2D context in CSS-pixel space (the device pixel ratio is
   *     already applied).
   * @param transform Coordinate transforms for this frame.
   */
  draw(ctx: CanvasRenderingContext2D, transform: CoordinateTransform): void;
}

/** One value of a study plotted in its own pane (display number only). */
export interface StudyPoint {
  /** Bar start (UTC ms); points for bars not on the chart are skipped. */
  timeMs: number;
  /** Plotted value. */
  value: number;
  /** Optional column color role (histograms only), e.g. bid for delta > 0. */
  tone?: OverlayTone;
  /**
   * Optional column opacity 0..1 (default 0.7), used with `tone`, e.g. faint
   * columns for estimated values next to measured ones.
   */
  opacity?: number;
}

/** Horizontal reference level inside a study pane (e.g. a threshold). */
export interface StudyLevel {
  /** Level value. */
  value: number;
  /** Axis label. */
  label: string;
  /** Color role. */
  tone: OverlayTone;
  /** Dash style. */
  lineStyle: OverlayLineStyle;
}

/**
 * Backend-computed values rendered in a separate pane below the price pane
 * (for example order-flow Delta %). The chart only draws them.
 */
export interface StudyPaneSpec {
  /** Unique id, e.g. `order_flow_pressure:delta_pct`. */
  id: string;
  /** Short title painted in the pane. */
  title: string;
  /** Columns or a line. */
  style: 'histogram' | 'line';
  /** Values per bar. */
  points: readonly StudyPoint[];
  /** Reference levels. */
  levels?: readonly StudyLevel[];
  /** Decimal places on the pane's axis (default 2). */
  precision?: number;
  /** Provenance caption such as `as of 12:05 UTC (live)`. */
  caption?: string;
}

/**
 * Another instrument overlaid on the price pane for comparison. It uses its
 * own percentage axis on the left, so different price levels line up.
 */
export interface CompareSeriesSpec {
  /** Unique id (usually the instrument id). */
  id: string;
  /** Legend label. */
  label: string;
  /** Line color (CSS). */
  color: string;
  /** Display closes by bar start; bars not on the main series are skipped. */
  points: ReadonlyArray<{timeMs: number; value: number}>;
}

/** Serializable view state for workspace persistence. */
export interface ChartViewState {
  /** Visible range, if any. */
  visibleRange: TimeRange | null;
}

/** The chart abstraction implemented by the LWC adapter and test fakes. */
export interface ChartPort {
  /**
   * Mounts into a container.
   * @param container Host element (sized by CSS).
   * @param theme Theme colors, applied once at mount.
   */
  mount(container: HTMLElement, theme: ChartTheme): void;
  /**
   * Sets series identity/presentation. When the instrument or timeframe
   * changes, clears the bars, overlay layers, study panes and comparisons,
   * so nothing from the previous venue stays on the chart.
   * @param config Series configuration.
   */
  setSeries(config: SeriesConfig): void;
  /**
   * Replaces all bars; the adapter sorts them and keeps the last bar given
   * for each time.
   * @param bars Bars in any order.
   */
  setBars(bars: readonly ChartBar[]): void;
  /**
   * Updates or appends the latest bar; out-of-order older bars are ignored.
   * @param bar Bar to apply.
   */
  updateBar(bar: ChartBar): void;
  /**
   * Adds an overlay layer or replaces the one with the same `layerId`.
   * @param layer Layer snapshot.
   */
  setOverlayLayer(layer: OverlayLayer): void;
  /**
   * Removes an overlay layer.
   * @param layerId Layer id.
   */
  removeOverlayLayer(layerId: string): void;
  /**
   * Adds a custom canvas layer or replaces the one with the same `id`.
   * @param layer Canvas layer.
   * @return Disposer removing the layer.
   */
  addCanvasLayer(layer: CanvasLayer): Disposer;
  /** Requests a repaint of canvas layers on the next frame. */
  invalidate(): void;
  /**
   * Returns transforms valid right now.
   * @return Coordinate transform, or null until both `mount` and `setSeries`
   *     have run.
   */
  transform(): CoordinateTransform | null;
  /**
   * Subscribes to an event.
   * @param event Event name.
   * @param handler Called with the event payload.
   * @return Disposer that unsubscribes the handler.
   */
  on<E extends ChartEventName>(
    event: E,
    handler: (payload: ChartEventMap[E]) => void
  ): Disposer;
  /**
   * Shows a synchronized crosshair from another chart. It is not emitted as
   * `crosshairMove`, so synchronized charts do not echo each other.
   * @param point Time (UTC ms) and display price, or null to hide.
   */
  setCrosshair(point: {timeMs: number; price: number} | null): void;
  /**
   * Sets the visible range.
   * @param range Time range (the LWC adapter ignores it while it has no bars).
   */
  setVisibleRange(range: TimeRange): void;
  /** Fits all bars into view. */
  fitContent(): void;
  /**
   * Adds or replaces a study pane below the price (and volume) pane.
   * @param spec Study values and presentation.
   */
  setStudyPane(spec: StudyPaneSpec): void;
  /**
   * Removes a study pane.
   * @param id Study id.
   */
  removeStudyPane(id: string): void;
  /**
   * Replaces the comparison overlays (empty hides the comparison axis).
   * @param series Instruments to compare.
   */
  setCompareSeries(series: readonly CompareSeriesSpec[]): void;
  /**
   * Enables or disables built-in pan/zoom (disabled while a tool drags).
   * @param enabled Whether interaction is enabled.
   */
  setInteractive(enabled: boolean): void;
  /**
   * Captures view state.
   * @return Serializable state.
   */
  serialize(): ChartViewState;
  /**
   * Restores view state.
   * @param state Saved state.
   */
  restore(state: ChartViewState): void;
  /**
   * Renders a PNG screenshot.
   * @return Data URL or null when unsupported.
   */
  screenshot(): string | null;
  /** Releases every listener, primitive and DOM node. */
  destroy(): void;
}
