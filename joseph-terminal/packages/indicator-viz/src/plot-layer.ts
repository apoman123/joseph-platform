/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Draws a plugin's price-pane plots (`display.plots` with
 * `pane: 'price'`, such as moving averages or bands) as polylines on a
 * chart canvas layer.
 *
 * Render-only: the values come from the engine's `/v1/indicators/series`;
 * a null value leaves a gap. The layer uses the chart port's canvas layer
 * hook with its per-frame coordinate transforms, so lines follow pans,
 * zooms and resizes.
 */

import {
  toneColor,
  type CanvasLayer,
  type ChartTheme,
  type CoordinateTransform,
  type OverlayLineStyle,
} from '@joseph/chart-core';
import type {IndicatorPluginInfo, IndicatorSeries} from '@joseph/contracts';
import {toneOf} from './mapping';
import {panePlots, plotPoints, type PlotPoint} from './study';

/** One polyline on the price pane. */
export interface PlotLine {
  /** Value key the line plots. */
  id: string;
  /** Points oldest first; null values are gaps. */
  points: readonly PlotPoint[];
  /** CSS color. */
  color: string;
  /** Dash style. */
  lineStyle: OverlayLineStyle;
  /** Width in CSS pixels. */
  lineWidth: number;
}

/** Options for building a plugin's price lines. */
export interface PlotLineOptions {
  /** Theme the tones resolve against. */
  theme: ChartTheme;
  /** Replay cursor (UTC ms); bars starting at or after it are dropped. */
  atMs?: number | null;
  /** User colors per value key (`#rrggbb`); they win over the metadata. */
  colors?: Readonly<Record<string, string>>;
}

/**
 * Returns a color when it is a valid `#rrggbb`.
 * @param color Candidate color.
 * @return The color, or undefined.
 */
function hexColor(color: string | null | undefined): string | undefined {
  return color && /^#[0-9a-f]{6}$/i.test(color) ? color : undefined;
}

/**
 * Builds the price-pane lines of one plugin from its series.
 * @param info Plugin descriptor.
 * @param series Engine series of the plugin.
 * @param options Theme, replay cursor and user colors.
 * @return One line per plot declared for the price pane, in declaration
 *     order; the color is the user's, else the plot's, else its tone's.
 */
export function plotLinesFromSeries(
  info: IndicatorPluginInfo,
  series: IndicatorSeries,
  options: PlotLineOptions
): PlotLine[] {
  return panePlots(info, 'price').map(plot => ({
    id: plot.value,
    points: plotPoints(series, plot.value, options.atMs ?? null),
    color:
      hexColor(options.colors?.[plot.value]) ??
      hexColor(plot.color) ??
      toneColor(options.theme, toneOf(plot.tone ?? 'accent', null)),
    lineStyle: plot.line_style ?? 'solid',
    lineWidth: plot.line_width ?? 1,
  }));
}

/**
 * Returns the canvas dash pattern of a line style.
 * @param style Dash style.
 * @param width Line width in CSS pixels.
 * @return Segment lengths for `setLineDash`.
 */
function dashOf(style: OverlayLineStyle, width: number): number[] {
  if (style === 'dashed') return [6 * width, 4 * width];
  return style === 'dotted' ? [width, 3 * width] : [];
}

/**
 * Paints polylines; a point without a value or coordinate breaks the line.
 * @param ctx Context in CSS pixels.
 * @param transform Frame transform.
 * @param lines Lines to paint.
 */
export function paintPlotLines(
  ctx: CanvasRenderingContext2D,
  transform: CoordinateTransform,
  lines: readonly PlotLine[]
): void {
  ctx.save();
  ctx.lineJoin = 'round';
  for (const line of lines) {
    ctx.strokeStyle = line.color;
    ctx.lineWidth = line.lineWidth;
    ctx.setLineDash(dashOf(line.lineStyle, line.lineWidth));
    ctx.beginPath();
    let drawing = false;
    for (const point of line.points) {
      const x = transform.timeToX(point.timeMs);
      const y = point.value === null ? null : transform.priceToY(point.value);
      if (x === null || y === null) {
        drawing = false;
        continue;
      }
      if (drawing) {
        ctx.lineTo(x, y);
      } else {
        ctx.moveTo(x, y);
        drawing = true;
      }
    }
    ctx.stroke();
  }
  ctx.restore();
}

/** Options of a plot layer. */
export interface PlotLayerOptions {
  /** Unique canvas layer id. */
  id: string;
  /** Paint order among canvas layers. */
  zIndex: number;
  /**
   * Returns the lines to paint, read on every frame.
   * @return Lines.
   */
  lines: () => readonly PlotLine[];
}

/**
 * Creates a canvas layer that paints price-pane plot lines.
 * @param options Id, paint order and the line accessor.
 * @return Canvas layer.
 */
export function createPlotLayer(options: PlotLayerOptions): CanvasLayer {
  return {
    id: options.id,
    zIndex: options.zIndex,
    draw(ctx, transform) {
      paintPlotLines(ctx, transform, options.lines());
    },
  };
}
