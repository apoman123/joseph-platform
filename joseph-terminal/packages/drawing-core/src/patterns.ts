/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Shared behaviour of multi-point chart patterns (XABCD,
 * ABCD, head and shoulders, Elliott waves, triangle pattern).
 *
 * A pattern is a zig-zag through its anchors with a label per anchor,
 * optional dashed connectors carrying Fibonacci ratio labels, optional
 * filled polygons and optional guide lines. Ratios are pure geometry:
 * `|leg| / |reference leg|` of the anchor prices, in exact decimals.
 * Each pattern tool lives in its own directory and passes its spec here.
 */

import {
  Decimal,
  type DrawingAnchor,
  type DrawingDocument,
} from '@joseph/contracts';
import {anchorPoints} from './document';
import {distanceToPolyline, pointInPolygon, type Point} from './geometry';
import type {PixelTransform} from './surface';
import {
  applyStroke,
  drawHandles,
  drawLabel,
  hitHandles,
  strokePath,
  type RenderState,
  type ToolManifest,
  type ToolPlugin,
} from './tool';

/** A ratio label between two legs, e.g. AB / XA. */
export interface PatternRatio {
  /** Measured leg as anchor indices `[from, to]`. */
  leg: readonly [number, number];
  /** Reference leg as anchor indices `[from, to]`. */
  of: readonly [number, number];
  /** Dashed connector the label sits on, as anchor indices. */
  along: readonly [number, number];
}

/** An extra line drawn and hit-tested with the pattern. */
export interface PatternGuide {
  /** Vertices in CSS pixels. */
  points: Point[];
  /** Whether the guide is dashed. */
  dashed: boolean;
}

/** Specification of one pattern tool. */
export interface PatternSpec {
  /** Label per anchor (`''` for none). */
  labels: readonly string[];
  /** Ratio labels on dashed connectors. */
  ratios?: readonly PatternRatio[];
  /** Polygons (anchor indices) filled with `style.fill_color`. */
  fills?: ReadonlyArray<readonly number[]>;
  /**
   * Derives guide lines from the anchor pixels (necklines, boundaries).
   * @param points Anchor points in CSS pixels (complete pattern).
   * @param transform Transforms of the current frame (for the plot size).
   * @return Guides.
   */
  guides?: (
    points: readonly Point[],
    transform: PixelTransform
  ) => PatternGuide[];
  /**
   * Derives extra filled shapes from the anchor pixels (for example the
   * area between two boundary lines), filled like `fills`.
   * @param points Anchor points in CSS pixels (complete pattern).
   * @param transform Transforms of the current frame (for the plot size).
   * @return Polygons in CSS pixels.
   */
  fillShapes?: (
    points: readonly Point[],
    transform: PixelTransform
  ) => Point[][];
  /**
   * Returns the object-list description when the pattern has no ratios.
   * @param anchors Anchors of the drawing.
   * @return Description.
   */
  describe?: (anchors: readonly DrawingAnchor[]) => string;
}

/**
 * Returns the exact ratio `|leg| / |reference|` of anchor prices, to three
 * decimals.
 * @param anchors Anchors of the drawing.
 * @param leg Measured leg as anchor indices.
 * @param of Reference leg as anchor indices.
 * @return Ratio text, or null when an anchor is missing or the reference
 *     leg is flat.
 * @throws `RangeError` when a used anchor price is not a decimal string.
 */
export function legRatio(
  anchors: readonly DrawingAnchor[],
  leg: readonly [number, number],
  of: readonly [number, number]
): string | null {
  const [a, b, c, d] = [
    anchors[leg[0]],
    anchors[leg[1]],
    anchors[of[0]],
    anchors[of[1]],
  ];
  if (!a || !b || !c || !d) return null;
  const reference = Decimal.parse(d.price).sub(Decimal.parse(c.price)).abs();
  if (reference.isZero()) return null;
  return Decimal.parse(b.price)
    .sub(Decimal.parse(a.price))
    .abs()
    .div(reference)
    .toFixed(3);
}

/**
 * Returns the vertical label offset for an anchor: above a local high,
 * below a local low (screen y grows downward).
 * @param points Anchor points in CSS pixels.
 * @param index Anchor index.
 * @return Offset in CSS pixels, -12 (above) or 12 (below); above when the
 *     anchor has no neighbours.
 */
export function labelOffset(points: readonly Point[], index: number): number {
  const point = points[index];
  if (!point) return -12;
  const neighbours = [points[index - 1], points[index + 1]].filter(
    (p): p is Point => p !== undefined
  );
  if (neighbours.length === 0) return -12;
  const mean = neighbours.reduce((sum, p) => sum + p.y, 0) / neighbours.length;
  return point.y <= mean ? -12 : 12;
}

/**
 * Returns the pixel polygons to fill: the spec's anchor-index fills
 * (skipping incomplete ones) plus its derived shapes once the pattern is
 * complete.
 * @param spec Pattern spec.
 * @param points Anchor points in CSS pixels.
 * @param transform Transforms of the current frame.
 * @param complete Whether every anchor is placed.
 * @return Polygons in CSS pixels with at least three vertices each.
 */
export function patternFillPolygons(
  spec: PatternSpec,
  points: readonly Point[],
  transform: PixelTransform,
  complete: boolean
): Point[][] {
  const indexed = (spec.fills ?? [])
    .map(indices => indices.map(i => points[i]))
    .filter((poly): poly is Point[] => poly.every(p => p !== undefined));
  const derived = complete ? (spec.fillShapes?.(points, transform) ?? []) : [];
  return [...indexed, ...derived].filter(poly => poly.length >= 3);
}

/**
 * Returns a one-line summary of a pattern's ratios, e.g.
 * `AB/XA 0.618, BC/AB 0.500`.
 * @param spec Pattern spec.
 * @param anchors Anchors of the drawing.
 * @return Summary (empty when no ratio can be computed).
 * @throws `RangeError` when a used anchor price is not a decimal string.
 */
export function ratioSummary(
  spec: PatternSpec,
  anchors: readonly DrawingAnchor[]
): string {
  return (spec.ratios ?? [])
    .map(ratio => {
      const value = legRatio(anchors, ratio.leg, ratio.of);
      const name = (i: number) => spec.labels[i] ?? String(i);
      return value === null
        ? null
        : `${name(ratio.leg[0])}${name(ratio.leg[1])}/${name(ratio.of[0])}${name(ratio.of[1])} ${value}`;
    })
    .filter((text): text is string => text !== null)
    .join(', ');
}

/**
 * Paints a pattern: fills, the zig-zag, ratio connectors, guides (once
 * complete), anchor labels and handles. Nothing is drawn unless there are
 * at least two anchors and all of them can be placed.
 * @param ctx 2D context in CSS-pixel space.
 * @param doc Drawing to paint.
 * @param transform Transforms of the frame being painted.
 * @param state Render state.
 * @param spec Pattern spec.
 * @param anchorCount Anchors of the complete pattern.
 */
export function renderPattern(
  ctx: CanvasRenderingContext2D,
  doc: DrawingDocument,
  transform: PixelTransform,
  state: RenderState,
  spec: PatternSpec,
  anchorCount: number
): void {
  const points = anchorPoints(transform, doc) ?? [];
  if (points.length < 2) return;
  const complete = points.length >= anchorCount;
  if (doc.style.fill_color) {
    ctx.fillStyle = doc.style.fill_color;
    for (const polygon of patternFillPolygons(
      spec,
      points,
      transform,
      complete
    )) {
      ctx.beginPath();
      polygon.forEach((p, i) =>
        i === 0 ? void ctx.moveTo(p.x, p.y) : void ctx.lineTo(p.x, p.y)
      );
      ctx.closePath();
      ctx.fill();
    }
  }
  applyStroke(ctx, doc.style, state);
  strokePath(ctx, points);
  const showLabels = doc.style.show_labels !== false;
  for (const ratio of spec.ratios ?? []) {
    const from = points[ratio.along[0]];
    const to = points[ratio.along[1]];
    if (!from || !to) continue;
    ctx.setLineDash([4, 4]);
    strokePath(ctx, [from, to]);
    const value = legRatio(doc.anchors, ratio.leg, ratio.of);
    if (showLabels && value !== null) {
      ctx.font = `11px ${state.palette.fontFamily}`;
      const width = ctx.measureText(value).width;
      drawLabel(
        ctx,
        value,
        (from.x + to.x) / 2 - width / 2,
        (from.y + to.y) / 2,
        doc.style.color,
        state
      );
    }
  }
  if (complete) {
    for (const guide of spec.guides?.(points, transform) ?? []) {
      applyStroke(ctx, doc.style, state);
      if (guide.dashed) ctx.setLineDash([6, 4]);
      strokePath(ctx, guide.points);
    }
  }
  if (showLabels) {
    points.forEach((point, i) => {
      const label = spec.labels[i];
      if (!label) return;
      ctx.font = `11px ${state.palette.fontFamily}`;
      const width = ctx.measureText(label).width;
      drawLabel(
        ctx,
        label,
        point.x - width / 2,
        point.y + labelOffset(points, i),
        doc.style.color,
        state
      );
    });
  }
  drawHandles(ctx, points, state);
}

/**
 * Hit-tests a pattern: handles first (handle index = anchor index), then
 * legs, ratio connectors, guides and (when filled) the inside of its
 * polygons.
 * @param doc Drawing to test.
 * @param transform Transforms of the current frame.
 * @param point Pointer position in CSS pixels.
 * @param tolerance Hit tolerance in CSS pixels.
 * @param spec Pattern spec.
 * @param anchorCount Anchors of the complete pattern (guides need all);
 *     defaults to the number of labels.
 * @return The part hit, or null for a miss.
 */
export function hitTestPattern(
  doc: DrawingDocument,
  transform: PixelTransform,
  point: Point,
  tolerance: number,
  spec: PatternSpec,
  anchorCount: number = spec.labels.length
): ReturnType<ToolPlugin['hitTest']> {
  const points = anchorPoints(transform, doc) ?? [];
  if (points.length < 2) return null;
  const handle = hitHandles(points, point, tolerance);
  if (handle) return handle;
  if (distanceToPolyline(point, points) <= tolerance) return {part: 'body'};
  for (const ratio of spec.ratios ?? []) {
    const from = points[ratio.along[0]];
    const to = points[ratio.along[1]];
    if (from && to && distanceToPolyline(point, [from, to]) <= tolerance) {
      return {part: 'body'};
    }
  }
  const complete = points.length >= anchorCount;
  if (complete) {
    for (const guide of spec.guides?.(points, transform) ?? []) {
      if (distanceToPolyline(point, guide.points) <= tolerance) {
        return {part: 'body'};
      }
    }
  }
  if (
    doc.style.fill_color &&
    patternFillPolygons(spec, points, transform, complete).some(polygon =>
      pointInPolygon(point, polygon)
    )
  ) {
    return {part: 'body'};
  }
  return null;
}

/**
 * Creates a pattern tool from a manifest and a spec.
 * @param manifest Manifest (`anchorCount` = anchors of the full pattern).
 * @param spec Pattern spec.
 * @param fill Default fill: `on` (the user mark color at 10% opacity),
 *     `off` (fill key present but null, so the style bar offers it) or
 *     `none` (no fill key).
 * @return Tool plugin.
 */
export function createPatternTool(
  manifest: ToolManifest,
  spec: PatternSpec,
  fill: 'on' | 'off' | 'none' = 'none'
): ToolPlugin {
  return {
    manifest,
    defaultStyle: palette => ({
      color: palette.user,
      line_width: 2,
      line_style: 'solid',
      show_labels: true,
      ...(fill === 'none'
        ? {}
        : {fill_color: fill === 'on' ? `${palette.user}1a` : null}),
    }),
    render(ctx, doc, transform, state) {
      renderPattern(ctx, doc, transform, state, spec, manifest.anchorCount);
    },
    hitTest(doc, transform, point, tolerance) {
      return hitTestPattern(
        doc,
        transform,
        point,
        tolerance,
        spec,
        manifest.anchorCount
      );
    },
    describe: doc => {
      const summary = ratioSummary(spec, doc.anchors);
      if (summary) return summary;
      return spec.describe?.(doc.anchors) ?? `${doc.anchors.length} points`;
    },
  };
}
