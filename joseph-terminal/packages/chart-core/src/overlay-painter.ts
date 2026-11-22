/**
 * @fileoverview Canvas painting of overlay boxes, rays and captions.
 *
 * Renderer-agnostic: the LWC adapter calls it from a series primitive and
 * tests call it with a recording context. Positions are recomputed from
 * time/price on every frame, so zoom and resize keep overlays anchored.
 */
import type {ChartTheme, CoordinateTransform} from './chart-port';
import type {
  BoxOverlay,
  OverlayLayer,
  OverlayLineStyle,
  PriceLineOverlay,
} from './overlay-model';
import {toneColor, withAlpha} from './theme';

/** Dash patterns per line style. */
const DASHES: Readonly<Record<OverlayLineStyle, number[]>> = {
  solid: [],
  dashed: [6, 4],
  dotted: [2, 3],
};

/** Pixel rectangle of a laid-out box. */
export interface BoxRect {
  /** Left edge. */
  x: number;
  /** Top edge. */
  y: number;
  /** Width. */
  width: number;
  /** Height (>= 1 so line-like zones stay visible). */
  height: number;
}

/**
 * Lays out a box overlay in pixels.
 * @param box Box model.
 * @param transform Coordinate transform.
 * @return Rectangle, or null when it cannot be placed.
 */
export function layoutBox(
  box: BoxOverlay,
  transform: CoordinateTransform
): BoxRect | null {
  const x1 = transform.timeToX(box.fromMs);
  const x2 = box.toMs === null ? transform.width : transform.timeToX(box.toMs);
  const yUpper = transform.priceToY(Number(box.upper));
  const yLower = transform.priceToY(Number(box.lower));
  if (x1 === null || x2 === null || yUpper === null || yLower === null) {
    return null;
  }
  const left = Math.min(x1, x2);
  const right = Math.max(x1, x2);
  if (right < 0 || left > transform.width) return null;
  const top = Math.min(yUpper, yLower);
  return {
    x: left,
    y: top,
    width: Math.max(1, right - left),
    height: Math.max(1, Math.abs(yLower - yUpper)),
  };
}

/**
 * Paints the box, ray and caption parts of overlay layers. Plain price lines
 * without `fromMs` and markers are drawn natively by the adapter.
 * @param ctx 2D context in CSS pixels.
 * @param layers Visible layers.
 * @param transform Coordinate transform.
 * @param theme Chart theme.
 */
export function paintOverlays(
  ctx: CanvasRenderingContext2D,
  layers: readonly OverlayLayer[],
  transform: CoordinateTransform,
  theme: ChartTheme
): void {
  const ordered = [...layers]
    .filter(layer => layer.visible)
    .sort((a, b) => a.zIndex - b.zIndex);
  ctx.save();
  ctx.font = `11px ${theme.fontFamily}`;
  for (const layer of ordered) {
    for (const model of layer.models) {
      if (model.kind === 'box') paintBox(ctx, model, transform, theme);
      if (model.kind === 'price-line' && model.fromMs !== undefined) {
        paintRay(ctx, model, transform, theme);
      }
    }
  }
  paintCaptions(ctx, ordered, transform, theme);
  ctx.restore();
}

/**
 * Paints one box.
 * @param ctx Context.
 * @param box Box model.
 * @param transform Transform.
 * @param theme Theme.
 */
function paintBox(
  ctx: CanvasRenderingContext2D,
  box: BoxOverlay,
  transform: CoordinateTransform,
  theme: ChartTheme
): void {
  const rect = layoutBox(box, transform);
  if (!rect) return;
  const color = box.color ?? toneColor(theme, box.tone);
  ctx.fillStyle = withAlpha(color, box.fillOpacity);
  ctx.fillRect(rect.x, rect.y, rect.width, rect.height);
  ctx.setLineDash(DASHES[box.lineStyle]);
  ctx.strokeStyle = withAlpha(color, 0.75);
  ctx.lineWidth = 1;
  ctx.strokeRect(rect.x + 0.5, rect.y + 0.5, rect.width - 1, rect.height - 1);
  if (box.midline) {
    const mid = rect.y + rect.height / 2;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(rect.x, mid);
    ctx.lineTo(rect.x + rect.width, mid);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  if (box.label && rect.width > 40 && rect.height > 12) {
    ctx.fillStyle = color;
    ctx.textBaseline = 'top';
    ctx.fillText(box.label, rect.x + 4, rect.y + 2);
  }
}

/**
 * Paints a price ray that starts at `fromMs`.
 * @param ctx Context.
 * @param line Price line model.
 * @param transform Transform.
 * @param theme Theme.
 */
function paintRay(
  ctx: CanvasRenderingContext2D,
  line: PriceLineOverlay,
  transform: CoordinateTransform,
  theme: ChartTheme
): void {
  const x = transform.timeToX(line.fromMs ?? 0);
  const y = transform.priceToY(Number(line.price));
  if (x === null || y === null) return;
  const color = line.color ?? toneColor(theme, line.tone);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.setLineDash(line.pending ? [4, 4] : DASHES[line.lineStyle]);
  ctx.beginPath();
  ctx.moveTo(Math.max(0, x), Math.round(y) + 0.5);
  ctx.lineTo(transform.width, Math.round(y) + 0.5);
  ctx.stroke();
  ctx.setLineDash([]);
  if (line.label) {
    ctx.fillStyle = color;
    ctx.textBaseline = 'bottom';
    const width = ctx.measureText(line.label).width;
    ctx.fillText(line.label, Math.max(4, transform.width - width - 8), y - 2);
  }
}

/**
 * Groups captions of the form `<name>, as of <provenance>` so layers that
 * share a timestamp produce one line.
 * @param captions Raw captions.
 * @return Display lines.
 */
export function groupCaptions(captions: readonly string[]): string[] {
  const groups = new Map<string, string[]>();
  const lines: string[] = [];
  for (const caption of captions) {
    const split = caption.indexOf(', as of ');
    if (split < 0) {
      lines.push(caption);
      continue;
    }
    const suffix = caption.slice(split + 2);
    const names = groups.get(suffix) ?? [];
    names.push(caption.slice(0, split));
    groups.set(suffix, names);
  }
  for (const [suffix, names] of groups) {
    lines.push(
      `${names.length === 1 ? names[0] : `${names.length} layers`} ${suffix}`
    );
  }
  return lines;
}

/**
 * Paints layer provenance captions at the top-left on a plate.
 * @param ctx Context.
 * @param layers Visible layers.
 * @param transform Transform.
 * @param theme Theme.
 */
function paintCaptions(
  ctx: CanvasRenderingContext2D,
  layers: readonly OverlayLayer[],
  transform: CoordinateTransform,
  theme: ChartTheme
): void {
  const lines = groupCaptions(
    layers
      .map(layer => layer.caption)
      .filter((text): text is string => Boolean(text))
  );
  ctx.textBaseline = 'middle';
  lines.forEach((line, index) => {
    const y = 14 + index * 16;
    const width = ctx.measureText(line).width;
    if (width + 16 > transform.width) return;
    ctx.fillStyle = withAlpha(theme.background, 0.85);
    ctx.fillRect(6, y - 8, width + 8, 16);
    ctx.fillStyle = theme.text;
    ctx.fillText(line, 10, y);
  });
}
