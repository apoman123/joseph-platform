/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Drawing document helpers over the contract
 * `DrawingDocument`: creation, conversion between anchors and pixels, and
 * exact translation.
 *
 * Anchors are data-space positions: `time_ms` in epoch ms UTC and `price`
 * as an exact decimal string, never pixels, so drawings stay put across
 * zoom, scroll and resize. Pixel points are CSS pixels relative to the plot
 * (see `surface.ts`).
 */

import {
  Decimal,
  type DrawingAnchor,
  type DrawingDocument,
  type DrawingStyle,
  type InstrumentId,
  type Timeframe,
} from '@joseph/contracts';
import type {Point} from './geometry';
import type {PixelTransform} from './surface';

/** Current drawing document schema version. */
export const DRAWING_SCHEMA_VERSION = 1;

/** Inputs for a new drawing. */
export interface NewDrawingInput {
  /** Tool id (`ToolManifest.id`). */
  toolId: string;
  /** Tool document version (`ToolManifest.version`). */
  toolVersion: number;
  /** Exact instrument the drawing belongs to. */
  instrumentId: InstrumentId;
  /** Timeframe the drawing is shown on, or null for all timeframes. */
  timeframe: Timeframe | null;
  /** Anchors in data space; copied, not shared. */
  anchors: DrawingAnchor[];
  /** Style; copied, not shared. */
  style: DrawingStyle;
  /** Tool-specific props; deep-copied. Defaults to `{}`. */
  props?: Record<string, unknown>;
  /** Text of text-bearing tools; defaults to null. */
  text?: string | null;
  /** Z index; higher draws on top. */
  zIndex: number;
  /** Creation time in epoch ms (also the first `updated_at_ms`). */
  nowMs: number;
  /** Returns a fresh drawing id. */
  newId: () => string;
}

/**
 * Creates a visible, unlocked drawing document at the current schema
 * version.
 * @param input Tool, instrument, anchors, style and clock of the drawing.
 * @return New document.
 */
export function createDrawing(input: NewDrawingInput): DrawingDocument {
  return {
    schema_version: DRAWING_SCHEMA_VERSION,
    drawing_id: input.newId(),
    tool_id: input.toolId,
    tool_version: input.toolVersion,
    instrument_id: input.instrumentId,
    timeframe: input.timeframe,
    anchors: input.anchors.map(anchor => ({...anchor})),
    style: {...input.style},
    text: input.text ?? null,
    props: input.props ? structuredClone(input.props) : {},
    locked: false,
    visible: true,
    z_index: input.zIndex,
    created_at_ms: input.nowMs,
    updated_at_ms: input.nowMs,
  };
}

/**
 * Formats a chart price as an anchor price, rounded to the tick.
 * @param price Price in quote units, as read from the chart (a float).
 * @param tickSize Tick size as a decimal string; no rounding when null.
 * @return Exact decimal string (at most 10 fractional digits before
 *     rounding to the tick).
 * @throws `RangeError` when `price` is not finite or `tickSize` is not a
 *     positive decimal string.
 */
export function anchorPrice(price: number, tickSize: string | null): string {
  const value = Decimal.fromNumber(price, 10);
  if (!tickSize) return value.toString();
  return value.roundToStep(Decimal.parse(tickSize)).toString();
}

/**
 * Converts a pixel point to an anchor.
 * @param transform Transforms of the current frame.
 * @param point Point in CSS pixels, plot-relative.
 * @param tickSize Tick size the price is rounded to, or null for none.
 * @return Anchor with the time rounded to a whole millisecond (never before
 *     the epoch) and a tick-rounded price, or null when the point has no
 *     time or finite price (no bars yet, or outside the price plot).
 * @throws `RangeError` when `tickSize` is not a positive decimal string.
 */
export function anchorFromPoint(
  transform: PixelTransform,
  point: Point,
  tickSize: string | null
): DrawingAnchor | null {
  const timeMs = transform.xToTime(point.x);
  const price = transform.yToPrice(point.y);
  if (timeMs === null || price === null || !Number.isFinite(price)) return null;
  return {
    time_ms: Math.max(0, Math.round(timeMs)),
    price: anchorPrice(price, tickSize),
  };
}

/**
 * Converts an anchor to pixels for the current frame.
 * @param transform Transforms of the current frame.
 * @param anchor Anchor in data space.
 * @return Point in CSS pixels (possibly off-screen), or null when the
 *     transform cannot place the time or price.
 */
export function anchorToPoint(
  transform: PixelTransform,
  anchor: DrawingAnchor
): Point | null {
  const x = transform.timeToX(anchor.time_ms);
  const y = transform.priceToY(Number(anchor.price));
  return x === null || y === null ? null : {x, y};
}

/**
 * Converts all anchors of a drawing to pixels.
 * @param transform Transforms of the current frame.
 * @param doc Drawing whose anchors are converted.
 * @return Points in CSS pixels in anchor order, or null if any anchor
 *     cannot be placed.
 */
export function anchorPoints(
  transform: PixelTransform,
  doc: DrawingDocument
): Point[] | null {
  const points: Point[] = [];
  for (const anchor of doc.anchors) {
    const point = anchorToPoint(transform, anchor);
    if (!point) return null;
    points.push(point);
  }
  return points;
}

/**
 * Returns whether a drawing is shown on a timeframe.
 * @param doc Drawing to check.
 * @param timeframe Active chart timeframe.
 * @return True when the drawing is scoped to all timeframes or to this one
 *     (its `visible` flag is not considered).
 */
export function appliesToTimeframe(
  doc: DrawingDocument,
  timeframe: Timeframe
): boolean {
  return doc.timeframe == null || doc.timeframe === timeframe;
}

/**
 * Returns a document with anchors shifted by a time/price delta.
 * @param doc Drawing to move; not modified.
 * @param deltaMs Time shift in milliseconds; shifted times are clamped to
 *     the epoch.
 * @param deltaPrice Price shift as a decimal string, added exactly.
 * @param nowMs Update time in epoch ms (`updated_at_ms`).
 * @return Moved copy.
 * @throws `RangeError` when `deltaPrice` or an anchor price is not a
 *     decimal string.
 */
export function translateDrawing(
  doc: DrawingDocument,
  deltaMs: number,
  deltaPrice: string,
  nowMs: number
): DrawingDocument {
  const delta = Decimal.parse(deltaPrice);
  return {
    ...doc,
    anchors: doc.anchors.map(anchor => ({
      time_ms: Math.max(0, anchor.time_ms + deltaMs),
      price: Decimal.parse(anchor.price).add(delta).toString(),
    })),
    updated_at_ms: nowMs,
  };
}
