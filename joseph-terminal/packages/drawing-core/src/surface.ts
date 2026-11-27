/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Renderer-agnostic surface drawings attach to.
 *
 * Structurally compatible with `ChartPort` from `@joseph/chart-core`, so the
 * drawing engine does not depend on any chart implementation.
 *
 * Coordinate spaces used throughout `@joseph/drawing-core`:
 *
 * - Pixels: CSS pixels relative to the top-left corner of the price plot,
 *   x growing to the right and y growing downward. Points off-screen have
 *   coordinates outside `[0, width] x [0, height]`.
 * - Time: epoch milliseconds, UTC.
 * - Price: a plain number in the instrument's quote units. Stored anchors
 *   keep prices as exact decimal strings; numbers are only used for
 *   placement on screen.
 */

/** Removes a registration. */
export type Disposer = () => void;

/** Pixel/data transforms valid for the current frame only. */
export interface PixelTransform {
  /** Returns whether the current price axis uses logarithmic coordinates. */
  isLog?(): boolean;
  /**
   * Converts a time to an x coordinate.
   * @param timeMs Any time in epoch ms UTC (bar start or between bars,
   *     past or future).
   * @return X in CSS pixels (may lie outside `[0, width]` when the time is
   *     off-screen), or null when the chart has no bars to place it against.
   */
  timeToX(timeMs: number): number | null;
  /**
   * Converts a price to a y coordinate.
   * @param price Price in quote units.
   * @return Y in CSS pixels (may lie outside `[0, height]`), or null while
   *     the price scale is not available.
   */
  priceToY(price: number): number | null;
  /**
   * Converts an x coordinate to a time.
   * @param x X in CSS pixels.
   * @return Time in epoch ms UTC, or null when the chart has no bars.
   */
  xToTime(x: number): number | null;
  /**
   * Converts a y coordinate to a price.
   * @param y Y in CSS pixels.
   * @return Price in quote units, or null outside the price plot (for
   *     example over a volume or study pane).
   */
  yToPrice(y: number): number | null;
  /** Plot width in CSS pixels. */
  readonly width: number;
  /** Plot height in CSS pixels. */
  readonly height: number;
}

/** Pointer event delivered by the surface. */
export interface SurfacePointerEvent {
  /** X in CSS pixels, plot-relative. */
  x: number;
  /** Y in CSS pixels, plot-relative (grows downward). */
  y: number;
  /** Time under the pointer in epoch ms UTC, or null without bars. */
  timeMs: number | null;
  /** Price under the pointer in quote units, or null outside the plot. */
  price: number | null;
  /** `PointerEvent.button`: 0 is the primary button. */
  button: number;
  /** Shift pressed (constrains angles). */
  shiftKey: boolean;
  /** Ctrl or Meta pressed (inverts magnet snapping for the gesture). */
  modKey: boolean;
  /** Prevents chart panning for this gesture. */
  preventDefault(): void;
}

/** Canvas layer drawn on every frame. */
export interface SurfaceCanvasLayer {
  /** Unique id. */
  readonly id: string;
  /** Paint order; higher draws on top. */
  readonly zIndex: number;
  /**
   * Paints the layer.
   * @param ctx 2D context in CSS-pixel space (device pixel ratio already
   *     applied).
   * @param transform Transforms of the frame being painted.
   */
  draw(ctx: CanvasRenderingContext2D, transform: PixelTransform): void;
}

/** Events the drawing engine consumes. */
export interface SurfaceEventMap {
  /** Pointer pressed. */
  pointerDown: SurfacePointerEvent;
  /** Pointer moved. */
  pointerMove: SurfacePointerEvent;
  /** Pointer released. */
  pointerUp: SurfacePointerEvent;
}

/** Subset of `ChartPort` used by the drawing engine. */
export interface DrawingSurface {
  /**
   * Subscribes to pointer events.
   * @param event Event name.
   * @param handler Called with each event.
   * @return Disposer that unsubscribes.
   */
  on<E extends keyof SurfaceEventMap>(
    event: E,
    handler: (payload: SurfaceEventMap[E]) => void
  ): Disposer;
  /**
   * Adds a canvas layer painted on every frame.
   * @param layer Layer to add.
   * @return Disposer that removes it.
   */
  addCanvasLayer(layer: SurfaceCanvasLayer): Disposer;
  /** Requests a repaint. */
  invalidate(): void;
  /**
   * Enables or disables chart pan and zoom.
   * @param enabled Whether the chart handles pan and zoom gestures.
   */
  setInteractive(enabled: boolean): void;
  /**
   * Returns the transforms of the current frame.
   * @return Transform, or null before the chart is mounted.
   */
  transform(): PixelTransform | null;
}

/** Bar prices used for magnet snapping (numbers, quote units). */
export interface SnapBar {
  /** Optional actual base volume; absent means unreported, not estimated. */
  volume?: number;
  /** Bar start in epoch ms UTC. */
  timeMs: number;
  /** Open price. */
  open: number;
  /** High price. */
  high: number;
  /** Low price. */
  low: number;
  /** Close price. */
  close: number;
}
