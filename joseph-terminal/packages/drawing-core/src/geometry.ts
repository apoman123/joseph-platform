/**
 * @fileoverview Pixel-space geometry and hit testing for drawings.
 */

/** Point in CSS pixels. */
export interface Point {
  /** X. */
  x: number;
  /** Y. */
  y: number;
}

/** Mouse hit tolerance in CSS pixels. */
export const MOUSE_TOLERANCE_PX = 6;
/** Touch hit tolerance in CSS pixels (fingers are imprecise). */
export const TOUCH_TOLERANCE_PX = 14;

/**
 * Euclidean distance.
 * @param a First point.
 * @param b Second point.
 * @return Distance in pixels.
 */
export function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/**
 * Distance from a point to a segment.
 * @param p Point.
 * @param a Segment start.
 * @param b Segment end.
 * @return Distance in pixels.
 */
export function distanceToSegment(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return distance(p, a);
  const t = Math.max(
    0,
    Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSquared)
  );
  return distance(p, {x: a.x + t * dx, y: a.y + t * dy});
}

/**
 * Extends a segment to the plot edges.
 * @param a First point.
 * @param b Second point.
 * @param width Plot width.
 * @param extendLeft Extend beyond `a`.
 * @param extendRight Extend beyond `b`.
 * @return Possibly extended endpoints.
 */
export function extendSegment(
  a: Point,
  b: Point,
  width: number,
  extendLeft: boolean,
  extendRight: boolean
): [Point, Point] {
  const dx = b.x - a.x;
  if (dx === 0) return [a, b];
  const slope = (b.y - a.y) / dx;
  const atX = (x: number): Point => ({x, y: a.y + slope * (x - a.x)});
  const leftward = dx > 0 ? 0 : width;
  const rightward = dx > 0 ? width : 0;
  return [extendLeft ? atX(leftward) : a, extendRight ? atX(rightward) : b];
}

/** Axis-aligned rectangle. */
export interface Rect {
  /** Left. */
  x: number;
  /** Top. */
  y: number;
  /** Width. */
  width: number;
  /** Height. */
  height: number;
}

/**
 * Builds a normalized rectangle from two corners.
 * @param a Corner.
 * @param b Opposite corner.
 * @return Rectangle with non-negative size.
 */
export function rectFromPoints(a: Point, b: Point): Rect {
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    width: Math.abs(b.x - a.x),
    height: Math.abs(b.y - a.y),
  };
}

/**
 * Whether a point lies inside a rectangle expanded by a tolerance.
 * @param p Point.
 * @param rect Rectangle.
 * @param tolerance Expansion in pixels.
 * @return True when inside.
 */
export function pointInRect(p: Point, rect: Rect, tolerance = 0): boolean {
  return (
    p.x >= rect.x - tolerance &&
    p.x <= rect.x + rect.width + tolerance &&
    p.y >= rect.y - tolerance &&
    p.y <= rect.y + rect.height + tolerance
  );
}

/**
 * Distance from a point to a rectangle's border.
 * @param p Point.
 * @param rect Rectangle.
 * @return Distance in pixels.
 */
export function distanceToRectBorder(p: Point, rect: Rect): number {
  const corners: Point[] = [
    {x: rect.x, y: rect.y},
    {x: rect.x + rect.width, y: rect.y},
    {x: rect.x + rect.width, y: rect.y + rect.height},
    {x: rect.x, y: rect.y + rect.height},
  ];
  return Math.min(
    ...corners.map((corner, i) =>
      distanceToSegment(p, corner, corners[(i + 1) % 4] ?? corner)
    )
  );
}

/**
 * Finds the index of the handle under a point.
 * @param p Point.
 * @param handles Handle positions.
 * @param tolerance Hit radius.
 * @return Handle index or -1.
 */
export function handleAt(
  p: Point,
  handles: readonly Point[],
  tolerance: number
): number {
  let best = -1;
  let bestDistance = Infinity;
  handles.forEach((handle, index) => {
    const d = distance(p, handle);
    if (d <= tolerance && d < bestDistance) {
      best = index;
      bestDistance = d;
    }
  });
  return best;
}
