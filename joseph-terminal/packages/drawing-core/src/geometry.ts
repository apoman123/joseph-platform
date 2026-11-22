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

/**
 * Reduces a sequence to at most `max` items, always keeping the first and
 * last and sampling the rest evenly (freehand strokes).
 * @param items Items in order.
 * @param max Maximum items (at least 2).
 * @return Reduced copy.
 */
export function decimate<T>(items: readonly T[], max: number): T[] {
  const limit = Math.max(2, Math.floor(max));
  if (items.length <= limit) return [...items];
  const result: T[] = [];
  const step = (items.length - 1) / (limit - 1);
  for (let i = 0; i < limit; i += 1) {
    const item = items[Math.round(i * step)];
    if (item !== undefined) result.push(item);
  }
  return result;
}

/**
 * Distance from a point to a polyline (optionally closed into a polygon).
 * @param p Point.
 * @param points Vertices in order.
 * @param closed Whether the last vertex connects back to the first.
 * @return Distance in pixels (Infinity for no vertices).
 */
export function distanceToPolyline(
  p: Point,
  points: readonly Point[],
  closed = false
): number {
  const first = points[0];
  if (points.length === 1 && first) return distance(p, first);
  let best = Infinity;
  const count = closed ? points.length : points.length - 1;
  for (let i = 0; i < count; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    if (a && b) best = Math.min(best, distanceToSegment(p, a, b));
  }
  return best;
}

/**
 * Whether a point lies inside a polygon (even-odd rule).
 * @param p Point.
 * @param polygon Vertices in order (implicitly closed).
 * @return True when inside.
 */
export function pointInPolygon(p: Point, polygon: readonly Point[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const a = polygon[i];
    const b = polygon[j];
    if (!a || !b) continue;
    if (
      a.y > p.y !== b.y > p.y &&
      p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x
    ) {
      inside = !inside;
    }
  }
  return inside;
}

/**
 * End point of a ray from `origin` through `through`, clipped to the plot
 * rectangle `[0, width] x [0, height]` (fans, pitchfork tines).
 * @param origin Ray start.
 * @param through Second point giving the direction.
 * @param width Plot width.
 * @param height Plot height.
 * @return Where the ray leaves the plot; `through` itself when the
 *     direction is zero or `through` already lies beyond the plot edge.
 */
export function rayToEdge(
  origin: Point,
  through: Point,
  width: number,
  height: number
): Point {
  const dx = through.x - origin.x;
  const dy = through.y - origin.y;
  if (dx === 0 && dy === 0) return through;
  const exits: number[] = [];
  if (dx > 0) exits.push((width - origin.x) / dx);
  if (dx < 0) exits.push(-origin.x / dx);
  if (dy > 0) exits.push((height - origin.y) / dy);
  if (dy < 0) exits.push(-origin.y / dy);
  const t = Math.min(...exits);
  if (!Number.isFinite(t) || t <= 1) return through;
  return {x: origin.x + dx * t, y: origin.y + dy * t};
}

/**
 * Intersection of two infinite lines.
 * @param a1 First line, point 1.
 * @param a2 First line, point 2.
 * @param b1 Second line, point 1.
 * @param b2 Second line, point 2.
 * @return Intersection, or null when the lines are parallel.
 */
export function lineIntersection(
  a1: Point,
  a2: Point,
  b1: Point,
  b2: Point
): Point | null {
  const d = (a1.x - a2.x) * (b1.y - b2.y) - (a1.y - a2.y) * (b1.x - b2.x);
  if (Math.abs(d) < 1e-9) return null;
  const t = ((a1.x - b1.x) * (b1.y - b2.y) - (a1.y - b1.y) * (b1.x - b2.x)) / d;
  return {x: a1.x + t * (a2.x - a1.x), y: a1.y + t * (a2.y - a1.y)};
}

/**
 * Samples an axis-aligned ellipse into a closed polygon.
 * @param center Centre.
 * @param rx Horizontal radius.
 * @param ry Vertical radius.
 * @param segments Number of vertices.
 * @return Vertices (the first is not repeated).
 */
export function sampleEllipse(
  center: Point,
  rx: number,
  ry: number,
  segments = 72
): Point[] {
  const points: Point[] = [];
  for (let i = 0; i < segments; i += 1) {
    const angle = (2 * Math.PI * i) / segments;
    points.push({
      x: center.x + rx * Math.cos(angle),
      y: center.y + ry * Math.sin(angle),
    });
  }
  return points;
}

/**
 * Circle through three points.
 * @param a First point.
 * @param b Second point.
 * @param c Third point.
 * @return Centre and radius, or null when the points are collinear.
 */
export function circumcircle(
  a: Point,
  b: Point,
  c: Point
): {center: Point; radius: number} | null {
  const d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y));
  if (Math.abs(d) < 1e-9) return null;
  const a2 = a.x * a.x + a.y * a.y;
  const b2 = b.x * b.x + b.y * b.y;
  const c2 = c.x * c.x + c.y * c.y;
  const center = {
    x: (a2 * (b.y - c.y) + b2 * (c.y - a.y) + c2 * (a.y - b.y)) / d,
    y: (a2 * (c.x - b.x) + b2 * (a.x - c.x) + c2 * (b.x - a.x)) / d,
  };
  return {center, radius: distance(center, a)};
}

/**
 * Samples the circular arc that starts at `a`, passes through `b` and ends
 * at `c`. Collinear points degrade to the straight segment `a`-`c`.
 * @param a Start.
 * @param b Point on the arc.
 * @param c End.
 * @param segments Number of segments.
 * @return Points from `a` to `c`.
 */
export function sampleArc(
  a: Point,
  b: Point,
  c: Point,
  segments = 48
): Point[] {
  const circle = circumcircle(a, b, c);
  if (!circle) return [a, c];
  const {center, radius} = circle;
  const tau = 2 * Math.PI;
  const angle = (p: Point) => Math.atan2(p.y - center.y, p.x - center.x);
  const start = angle(a);
  const offset = (p: Point) => (((angle(p) - start) % tau) + tau) % tau;
  const through = offset(b);
  const end = offset(c);
  // Sweep in whichever direction passes through `b`.
  const sweep = through <= end ? end : end - tau;
  const points: Point[] = [];
  for (let i = 0; i <= segments; i += 1) {
    const theta = start + (sweep * i) / segments;
    points.push({
      x: center.x + radius * Math.cos(theta),
      y: center.y + radius * Math.sin(theta),
    });
  }
  return points;
}

/**
 * Samples a quadratic Bézier curve.
 * @param a Start.
 * @param control Control point.
 * @param b End.
 * @param segments Number of segments.
 * @return Points from `a` to `b`.
 */
export function sampleQuadratic(
  a: Point,
  control: Point,
  b: Point,
  segments = 32
): Point[] {
  const points: Point[] = [];
  for (let i = 0; i <= segments; i += 1) {
    const t = i / segments;
    const u = 1 - t;
    points.push({
      x: u * u * a.x + 2 * u * t * control.x + t * t * b.x,
      y: u * u * a.y + 2 * u * t * control.y + t * t * b.y,
    });
  }
  return points;
}
