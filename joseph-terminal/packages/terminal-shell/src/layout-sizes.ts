/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Resizable layout geometry (pure): chart-grid track sizes for
 * the multi-chart splitters and clamped dock sizes for the side and bottom
 * panels.
 */

import type {LayoutId} from '@joseph/contracts';

/** Track sizes of a chart grid as fractions that sum to 1. */
export interface GridSizes {
  /** Column fractions, left to right. */
  cols: readonly number[];
  /** Row fractions, top to bottom. */
  rows: readonly number[];
}

/** Smallest share of the grid a chart column or row may shrink to. */
export const MIN_TRACK_SHARE = 0.15;

/** Column and row counts per layout. */
export const LAYOUT_TRACKS: Readonly<
  Record<LayoutId, {cols: number; rows: number}>
> = {
  '1': {cols: 1, rows: 1},
  '2h': {cols: 2, rows: 1},
  '2v': {cols: 1, rows: 2},
  '4': {cols: 2, rows: 2},
  '8': {cols: 4, rows: 2},
};

/**
 * Returns equal track fractions.
 * @param count Number of tracks.
 * @return `count` fractions of `1 / count` each.
 */
function equal(count: number): number[] {
  return Array.from({length: count}, () => 1 / count);
}

/**
 * Returns valid grid sizes for a layout, falling back to equal tracks when
 * the saved sizes do not match it. Saved columns and rows are each kept when
 * they have one fraction per track, each at least half of
 * `MIN_TRACK_SHARE`, summing to 1 within 0.01.
 * @param layout Layout the sizes are for.
 * @param saved Saved sizes (untrusted).
 * @return Sizes; columns and rows fall back independently.
 */
export function gridSizesFor(layout: LayoutId, saved: unknown): GridSizes {
  const tracks = LAYOUT_TRACKS[layout];
  const valid = (list: unknown, count: number): list is number[] =>
    Array.isArray(list) &&
    list.length === count &&
    list.every(v => typeof v === 'number' && v >= MIN_TRACK_SHARE / 2) &&
    Math.abs(list.reduce((sum: number, v: number) => sum + v, 0) - 1) < 0.01;
  const candidate = (saved ?? {}) as Partial<GridSizes>;
  return {
    cols: valid(candidate.cols, tracks.cols)
      ? candidate.cols
      : equal(tracks.cols),
    rows: valid(candidate.rows, tracks.rows)
      ? candidate.rows
      : equal(tracks.rows),
  };
}

/**
 * Moves the boundary after track `index` by `delta` (fraction of the grid),
 * keeping both neighbours at least `MIN_TRACK_SHARE`. Other tracks and the
 * pair's total do not change.
 * @param tracks Current fractions.
 * @param index Track left of / above the splitter.
 * @param delta Fraction to move (positive grows `index`).
 * @return New fractions; an unchanged copy when `index` has no right or
 *     lower neighbour.
 */
export function resizeTracks(
  tracks: readonly number[],
  index: number,
  delta: number
): number[] {
  const next = [...tracks];
  const a = next[index];
  const b = next[index + 1];
  if (a === undefined || b === undefined) return next;
  const pair = a + b;
  const grown = Math.min(
    pair - MIN_TRACK_SHARE,
    Math.max(MIN_TRACK_SHARE, a + delta)
  );
  next[index] = grown;
  next[index + 1] = pair - grown;
  return next;
}

/**
 * Formats fractions as a CSS grid template. `minmax(0, ...)` lets a track
 * shrink below its content's minimum size, so a chart never forces its
 * track wider than its share.
 * @param tracks Fractions.
 * @return Template such as `minmax(0, 0.6000fr) minmax(0, 0.4000fr)`.
 */
export function tracksToTemplate(tracks: readonly number[]): string {
  return tracks.map(t => `minmax(0, ${t.toFixed(4)}fr)`).join(' ');
}

/**
 * Clamps a dock size in CSS pixels.
 * @param size Requested size.
 * @param min Smallest allowed size.
 * @param max Largest allowed size; wins when `min` exceeds it.
 * @return Clamped size, rounded to whole pixels.
 */
export function clampDock(size: number, min: number, max: number): number {
  return Math.round(Math.min(max, Math.max(min, size)));
}
