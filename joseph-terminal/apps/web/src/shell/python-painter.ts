/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Safe declarative Python visuals and fill-bar strategy arrows. */

import type {CanvasLayer, CoordinateTransform} from '@joseph/chart-core';
import {
  TIMEFRAME_MS,
  type ScriptResult,
  type ScriptVisual,
  type Timeframe,
} from '@joseph/contracts';

/**
 * Accepts only literal hex colors; user output cannot inject CSS or URLs.
 * @param value Untrusted declared color.
 * @param fallback Safe fallback hex color.
 * @return Valid hex color or fallback.
 */
export function pythonColor(value: unknown, fallback = '#5cb8e6'): string {
  return typeof value === 'string' &&
    /^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(value)
    ? value
    : fallback;
}

/**
 * Reads a finite numeric coordinate from untrusted script output.
 * @param value Worker output coordinate.
 * @return Finite bounded display number or null.
 */
function numeric(value: unknown): number | null {
  return typeof value === 'number' &&
    Number.isFinite(value) &&
    Math.abs(value) < 1e16
    ? value
    : null;
}

/**
 * Converts a bounded plain-text label without interpreting markup.
 * @param value Worker-provided text or scalar.
 * @return At most three hundred display characters.
 */
function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number'
    ? String(value).slice(0, 300)
    : '';
}

/**
 * Paints one declarative drawing using the pane's native coordinate transform.
 * @param ctx Canvas context already clipped to the current pane.
 * @param transform Pane-specific time/value transforms.
 * @param visual Validated worker drawing; individual fields remain untrusted.
 * @param result Run output used to resolve fills between named plots or levels.
 */
function paintVisual(
  ctx: CanvasRenderingContext2D,
  transform: CoordinateTransform,
  visual: ScriptVisual,
  result: ScriptResult
): void {
  const d = visual.data;
  const kind = visual.kind;
  const x = (value: unknown) => {
    const n = numeric(value);
    return n === null ? null : transform.timeToX(n);
  };
  const y = (value: unknown) => {
    const n = numeric(value);
    return n === null ? null : transform.priceToY(n);
  };
  const color = pythonColor(d.color);
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = Math.min(5, Math.max(1, numeric(d.width) ?? 1));
  ctx.setLineDash(
    d.style === 'dotted' ? [2, 3] : d.style === 'dashed' ? [6, 4] : []
  );
  if (kind === 'table') {
    if (!Array.isArray(d.rows)) return;
    const rows = d.rows
      .slice(0, 20)
      .filter((row): row is unknown[] => Array.isArray(row));
    const columns = Math.min(10, Math.max(1, ...rows.map(row => row.length)));
    const width = Math.min(transform.width, columns * 100);
    const height = rows.length * 21;
    const position = text(d.position);
    const left = position.endsWith('left')
      ? 5
      : position.endsWith('center')
        ? (transform.width - width) / 2
        : transform.width - width - 5;
    const top = position.startsWith('bottom')
      ? transform.height - height - 5
      : position.startsWith('middle')
        ? (transform.height - height) / 2
        : 38;
    ctx.fillStyle = pythonColor(d.background, '#122431');
    ctx.fillRect(left, top, width, height);
    ctx.fillStyle = pythonColor(d.color, '#e4edf1');
    ctx.font = '11px monospace';
    rows.forEach(
      (row, r) =>
        void row
          .slice(0, 10)
          .forEach(
            (cell, c) =>
              void ctx.fillText(
                text(cell),
                left + (c * width) / columns + 4,
                top + r * 21 + 14,
                width / columns - 8
              )
          )
    );
    return;
  }
  if (kind === 'linefill') {
    const first = result.visuals?.find(
      row => row.id === d.first && row.kind === 'line'
    );
    const second = result.visuals?.find(
      row => row.id === d.second && row.kind === 'line'
    );
    if (!first || !second) return;
    const points = [
      first.data.from_ms,
      first.data.to_ms,
      second.data.from_ms,
      second.data.to_ms,
      first.data.from_price,
      first.data.to_price,
      second.data.from_price,
      second.data.to_price,
    ].map(numeric);
    if (points.some(value => value === null)) return;
    const [a = 0, b = 0, c = 0, e = 0, ap = 0, bp = 0, cp = 0, ep = 0] =
      points as number[];
    const left = Math.max(Math.min(a, b), Math.min(c, e));
    const right = Math.min(Math.max(a, b), Math.max(c, e));
    if (right <= left || a === b || c === e) return;
    const at = (
      time: number,
      start: number,
      end: number,
      p: number,
      q: number
    ) => p + ((q - p) * (time - start)) / (end - start);
    paintVisual(
      ctx,
      transform,
      {
        id: visual.id,
        kind: 'polyline',
        data: {
          points: [
            [left, at(left, a, b, ap, bp)],
            [right, at(right, a, b, ap, bp)],
            [right, at(right, c, e, cp, ep)],
            [left, at(left, c, e, cp, ep)],
          ],
          closed: true,
          fill: color,
          color,
          opacity: d.opacity,
        },
      },
      result
    );
    return;
  }
  if (kind === 'fill') {
    const series = (name: unknown) => {
      const found = result.plots.find(plot => plot.name === name);
      if (found) return found.points;
      const level = result.visuals?.find(
        row => row.kind === 'hline' && row.id === name
      );
      return level
        ? (
            result.plots[0]?.points ??
            Object.keys(result.bar_closes ?? {}).map(time => ({
              time_ms: Number(time),
              value: null,
            }))
          ).map(point => ({
            ...point,
            value: numeric(level.data.price),
          }))
        : [];
    };
    const first = series(d.first);
    const second = new Map(
      series(d.second).map(point => [point.time_ms, point.value])
    );
    const pairs = first.flatMap(point => {
      const a = y(point.value);
      const b = y(second.get(point.time_ms));
      const xx = x(point.time_ms);
      return xx === null || a === null || b === null ? [] : [{x: xx, a, b}];
    });
    if (pairs.length < 2) return;
    ctx.globalAlpha = Math.min(1, Math.max(0, numeric(d.opacity) ?? 0.15));
    ctx.beginPath();
    pairs.forEach((point, index) => {
      if (index === 0) ctx.moveTo(point.x, point.a);
      else ctx.lineTo(point.x, point.a);
    });
    [...pairs].reverse().forEach(point => void ctx.lineTo(point.x, point.b));
    ctx.closePath();
    ctx.fill();
    return;
  }
  if (kind === 'hline') {
    const yy = y(d.price);
    if (yy !== null) {
      ctx.beginPath();
      ctx.moveTo(0, yy);
      ctx.lineTo(transform.width, yy);
      ctx.stroke();
      ctx.fillText(text(d.text), 5, yy - 4);
    }
    return;
  }
  if (kind === 'polyline') {
    if (!Array.isArray(d.points)) return;
    const points = d.points.slice(0, 64).flatMap(point => {
      if (!Array.isArray(point)) return [];
      const xx = x(point[0]);
      const yy = y(point[1]);
      return xx === null || yy === null ? [] : [{x: xx, y: yy}];
    });
    ctx.beginPath();
    points.forEach((p, i) => {
      if (!i) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    if (d.closed) ctx.closePath();
    if (d.fill) {
      ctx.fillStyle = pythonColor(d.fill);
      ctx.globalAlpha = Math.min(1, Math.max(0, numeric(d.opacity) ?? 0.15));
      ctx.fill();
      ctx.globalAlpha = 1;
    }
    ctx.stroke();
    return;
  }
  if (kind === 'line' || kind === 'box') {
    let a = x(d.from_ms);
    let b = x(d.to_ms);
    let ya = y(kind === 'box' ? d.lower : d.from_price);
    let yb = y(kind === 'box' ? d.upper : d.to_price);
    if (a === null || b === null || ya === null || yb === null) return;
    if (kind === 'box') {
      ctx.globalAlpha = Math.min(1, Math.max(0, numeric(d.opacity) ?? 0.15));
      ctx.fillRect(
        Math.min(a, b),
        Math.min(ya, yb),
        Math.abs(b - a),
        Math.abs(yb - ya)
      );
      ctx.globalAlpha = 1;
      ctx.strokeRect(
        Math.min(a, b),
        Math.min(ya, yb),
        Math.abs(b - a),
        Math.abs(yb - ya)
      );
      ctx.fillText(text(d.text), Math.min(a, b) + 4, Math.min(ya, yb) + 14);
      return;
    }
    const slope = a === b ? 0 : (yb - ya) / (b - a);
    if (d.extend === 'left' || d.extend === 'both') {
      ya -= a * slope;
      a = 0;
    }
    if (d.extend === 'right' || d.extend === 'both') {
      yb += (transform.width - b) * slope;
      b = transform.width;
    }
    ctx.beginPath();
    ctx.moveTo(a, ya);
    ctx.lineTo(b, yb);
    ctx.stroke();
    return;
  }
  const xx = x(d.time_ms);
  if (xx === null) return;
  const spacing = Math.max(
    2,
    Math.abs(
      (transform.timeToX(
        (numeric(d.time_ms) ?? 0) +
          (TIMEFRAME_MS[result.tf as Timeframe] ?? 60000)
      ) ?? xx + 7) - xx
    )
  );
  if (kind === 'bgcolor') {
    ctx.globalAlpha = Math.min(1, Math.max(0, numeric(d.opacity) ?? 0.15));
    ctx.fillRect(xx - spacing / 2, 0, spacing, transform.height);
    return;
  }
  if (kind === 'barcolor' || kind === 'candle' || kind === 'bar') {
    const o = y(d.open);
    const h = y(d.high);
    const l = y(d.low);
    const c = y(d.close);
    if (o === null || h === null || l === null || c === null) return;
    const half = Math.max(1, Math.min(10, spacing * 0.32));
    ctx.beginPath();
    ctx.moveTo(xx, h);
    ctx.lineTo(xx, l);
    if (kind === 'bar') {
      ctx.moveTo(xx - half, o);
      ctx.lineTo(xx, o);
      ctx.moveTo(xx, c);
      ctx.lineTo(xx + half, c);
      ctx.stroke();
    } else {
      ctx.stroke();
      ctx.fillRect(
        xx - half,
        Math.min(o, c),
        half * 2,
        Math.max(1, Math.abs(c - o))
      );
    }
    return;
  }
  const anchor =
    d.position === 'above'
      ? (d.high ?? d.price)
      : d.position === 'below'
        ? (d.low ?? d.price)
        : d.price;
  let yy = y(anchor);
  if (yy === null) return;
  yy += d.position === 'above' ? -15 : d.position === 'below' ? 15 : 0;
  ctx.font = '11px monospace';
  if (kind === 'label' || d.shape === 'character') {
    ctx.fillText(text(d.text), xx + 4, yy);
    return;
  }
  if (kind === 'shape') {
    const shape = text(d.shape);
    ctx.beginPath();
    if (shape === 'arrow_up' || shape === 'arrow_down') {
      const sign = shape === 'arrow_up' ? 1 : -1;
      ctx.moveTo(xx, yy - sign * 6);
      ctx.lineTo(xx - 5, yy + sign * 3);
      ctx.lineTo(xx + 5, yy + sign * 3);
      ctx.closePath();
      ctx.fill();
    } else if (shape === 'square') ctx.fillRect(xx - 4, yy - 4, 8, 8);
    else if (shape === 'diamond') {
      ctx.moveTo(xx, yy - 5);
      ctx.lineTo(xx + 5, yy);
      ctx.lineTo(xx, yy + 5);
      ctx.lineTo(xx - 5, yy);
      ctx.closePath();
      ctx.fill();
    } else if (shape === 'cross') {
      ctx.moveTo(xx - 4, yy - 4);
      ctx.lineTo(xx + 4, yy + 4);
      ctx.moveTo(xx + 4, yy - 4);
      ctx.lineTo(xx - 4, yy + 4);
      ctx.stroke();
    } else {
      ctx.arc(xx, yy, 4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillText(text(d.text), xx + 7, yy);
  }
}

/**
 * Builds a canvas layer covering Python plots, all drawing categories and fills.
 * @param id Chart-local unique layer identity.
 * @param result Validated backend result for the selected source/version.
 * @param colors Per-series user style overrides.
 * @return Renderer using only finite coordinates, literal colors and plain text.
 */
export function pythonCanvas(
  id: string,
  result: ScriptResult,
  colors: Readonly<Record<string, string>> = {}
): CanvasLayer {
  return {
    id,
    zIndex: 20,
    draw(ctx, transform) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, transform.width, transform.height);
      ctx.clip();
      ctx.font = '11px monospace';
      const visuals = [
        ...(result.visuals ?? []),
        ...(result.bar_visuals ?? []),
      ];
      for (const visual of visuals.filter(
        row =>
          row.kind === 'bgcolor' ||
          row.kind === 'fill' ||
          row.kind === 'linefill'
      )) {
        ctx.save();
        paintVisual(ctx, transform, visual, result);
        ctx.restore();
      }
      for (const plot of result.plots) {
        const style = result.plot_styles?.[plot.name] ?? {};
        const color = pythonColor(colors[plot.name] ?? style.color);
        const offset = numeric(style.offset) ?? 0;
        const points = plot.points.map(point => {
          const xx = transform.timeToX(point.time_ms);
          const next = transform.timeToX(
            point.time_ms + (TIMEFRAME_MS[result.tf as Timeframe] ?? 60000)
          );
          const yy =
            point.value === null ? null : transform.priceToY(point.value);
          return {
            x: xx === null ? null : xx + ((next ?? xx) - xx) * offset,
            y: yy,
            color: point.color,
          };
        });
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = Math.min(5, Math.max(1, numeric(style.width) ?? 2));
        ctx.setLineDash([]);
        ctx.beginPath();
        let previous: {x: number; y: number} | null = null;
        let strokeColor = color;
        for (const point of points) {
          const currentColor = pythonColor(
            colors[plot.name] ?? point.color ?? style.color
          );
          if (currentColor !== strokeColor) {
            ctx.stroke();
            ctx.beginPath();
            if (previous) ctx.moveTo(previous.x, previous.y);
            strokeColor = currentColor;
          }
          ctx.strokeStyle = currentColor;
          ctx.fillStyle = currentColor;
          if (point.x === null || point.y === null) {
            previous = null;
            continue;
          }
          if (style.style === 'histogram' || style.style === 'columns') {
            const zero = transform.priceToY(0) ?? transform.height;
            ctx.fillRect(
              point.x - 2,
              Math.min(point.y, zero),
              4,
              Math.max(1, Math.abs(point.y - zero))
            );
          } else if (style.style === 'circles') {
            ctx.moveTo(point.x + 2, point.y);
            ctx.arc(point.x, point.y, 2, 0, Math.PI * 2);
          } else if (style.style === 'crosses') {
            ctx.moveTo(point.x - 3, point.y - 3);
            ctx.lineTo(point.x + 3, point.y + 3);
            ctx.moveTo(point.x + 3, point.y - 3);
            ctx.lineTo(point.x - 3, point.y + 3);
          } else {
            if (!previous) ctx.moveTo(point.x, point.y);
            else {
              if (style.style === 'step') ctx.lineTo(point.x, previous.y);
              ctx.lineTo(point.x, point.y);
            }
            if (style.style === 'area' && previous) {
              ctx.save();
              ctx.globalAlpha = 0.15;
              const zero = transform.priceToY(0) ?? transform.height;
              ctx.fillRect(
                previous.x,
                Math.min(point.y, zero),
                point.x - previous.x,
                Math.abs(point.y - zero)
              );
              ctx.restore();
            }
          }
          previous = {x: point.x, y: point.y};
        }
        ctx.stroke();
      }
      for (const visual of visuals.filter(
        row =>
          row.kind !== 'bgcolor' &&
          row.kind !== 'fill' &&
          row.kind !== 'linefill'
      )) {
        ctx.save();
        paintVisual(ctx, transform, visual, result);
        ctx.restore();
      }
      if (result.overlay) {
        for (const fill of result.fills) {
          const xx = transform.timeToX(fill.time_ms);
          const yy = transform.priceToY(fill.price);
          if (xx === null || yy === null) continue;
          const sign = fill.side === 'buy' ? 1 : -1;
          ctx.strokeStyle = fill.side === 'buy' ? '#3cc79a' : '#f0645a';
          ctx.fillStyle = ctx.strokeStyle;
          ctx.lineWidth = 2;
          ctx.setLineDash([]);
          ctx.beginPath();
          ctx.moveTo(xx, yy + sign * 25);
          ctx.lineTo(xx, yy);
          ctx.moveTo(xx - 5, yy + sign * 7);
          ctx.lineTo(xx, yy);
          ctx.lineTo(xx + 5, yy + sign * 7);
          ctx.stroke();
          ctx.fillText(
            fill.effect === 'reversal'
              ? 'Exit / Entry'
              : fill.effect === 'exit'
                ? 'Exit'
                : 'Entry',
            xx + 7,
            yy + sign * 22
          );
        }
      }
      ctx.restore();
    },
  };
}
