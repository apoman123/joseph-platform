/**
 * @fileoverview Drawing document helpers over the contract
 * `DrawingDocument`. Anchors are time/price (decimal strings), never pixels.
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
  /** Tool id. */
  toolId: string;
  /** Tool version. */
  toolVersion: number;
  /** Exact instrument the drawing belongs to. */
  instrumentId: InstrumentId;
  /** Timeframe, or null for all timeframes. */
  timeframe: Timeframe | null;
  /** Anchors. */
  anchors: DrawingAnchor[];
  /** Style. */
  style: DrawingStyle;
  /** Tool-specific props. */
  props?: Record<string, unknown>;
  /** Optional text. */
  text?: string | null;
  /** Z order. */
  zIndex: number;
  /** Creation time. */
  nowMs: number;
  /** Id generator. */
  newId: () => string;
}

/**
 * Creates a drawing document.
 * @param input Inputs.
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
 * @param price Display price.
 * @param tickSize Tick size string; no rounding when null.
 * @return Decimal string.
 */
export function anchorPrice(price: number, tickSize: string | null): string {
  const value = Decimal.fromNumber(price, 10);
  if (!tickSize) return value.toString();
  return value.roundToStep(Decimal.parse(tickSize)).toString();
}

/**
 * Converts a pixel point to an anchor.
 * @param transform Transform.
 * @param point Pixel point.
 * @param tickSize Tick size for rounding.
 * @return Anchor or null when outside the data space.
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
 * @param transform Transform.
 * @param anchor Anchor.
 * @return Pixel point or null.
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
 * Converts all anchors to pixels; null if any cannot be placed.
 * @param transform Transform.
 * @param doc Document.
 * @return Pixel points or null.
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
 * Whether a drawing is shown on a timeframe.
 * @param doc Document.
 * @param timeframe Active timeframe.
 * @return True when visible there.
 */
export function appliesToTimeframe(
  doc: DrawingDocument,
  timeframe: Timeframe
): boolean {
  return doc.timeframe == null || doc.timeframe === timeframe;
}

/**
 * Returns a document with anchors shifted by a time/price delta.
 * @param doc Document.
 * @param deltaMs Time delta.
 * @param deltaPrice Price delta (decimal string).
 * @param nowMs Update time.
 * @return Moved document.
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
