/**
 * @fileoverview Renderer-agnostic surface drawings attach to.
 *
 * Structurally compatible with `ChartPort` from `@joseph/chart-core`, so the
 * drawing engine does not depend on any chart implementation.
 */

/** Removes a registration. */
export type Disposer = () => void;

/** Pixel/data transforms for the current frame. */
export interface PixelTransform {
  /**
   * Time to x.
   * @param timeMs UTC ms.
   * @return X or null.
   */
  timeToX(timeMs: number): number | null;
  /**
   * Price to y.
   * @param price Price.
   * @return Y or null.
   */
  priceToY(price: number): number | null;
  /**
   * X to time.
   * @param x X.
   * @return UTC ms or null.
   */
  xToTime(x: number): number | null;
  /**
   * Y to price.
   * @param y Y.
   * @return Price or null.
   */
  yToPrice(y: number): number | null;
  /** Plot width. */
  readonly width: number;
  /** Plot height. */
  readonly height: number;
}

/** Pointer event delivered by the surface. */
export interface SurfacePointerEvent {
  /** X in CSS pixels. */
  x: number;
  /** Y in CSS pixels. */
  y: number;
  /** Time under the pointer. */
  timeMs: number | null;
  /** Price under the pointer. */
  price: number | null;
  /** Button. */
  button: number;
  /** Shift pressed (constrains angles). */
  shiftKey: boolean;
  /** Ctrl/Meta pressed. */
  modKey: boolean;
  /** Prevents chart panning for this gesture. */
  preventDefault(): void;
}

/** Canvas layer drawn on every frame. */
export interface SurfaceCanvasLayer {
  /** Unique id. */
  readonly id: string;
  /** Paint order. */
  readonly zIndex: number;
  /**
   * Paints the layer.
   * @param ctx 2D context.
   * @param transform Transform.
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
   * @param handler Handler.
   * @return Disposer.
   */
  on<E extends keyof SurfaceEventMap>(
    event: E,
    handler: (payload: SurfaceEventMap[E]) => void
  ): Disposer;
  /**
   * Adds a canvas layer.
   * @param layer Layer.
   * @return Disposer.
   */
  addCanvasLayer(layer: SurfaceCanvasLayer): Disposer;
  /** Requests a repaint. */
  invalidate(): void;
  /**
   * Enables or disables chart pan/zoom.
   * @param enabled Whether enabled.
   */
  setInteractive(enabled: boolean): void;
  /**
   * Current transform.
   * @return Transform or null.
   */
  transform(): PixelTransform | null;
}

/** OHLC used for magnet snapping. */
export interface SnapBar {
  /** Bar start. */
  timeMs: number;
  /** Open. */
  open: number;
  /** High. */
  high: number;
  /** Low. */
  low: number;
  /** Close. */
  close: number;
}
