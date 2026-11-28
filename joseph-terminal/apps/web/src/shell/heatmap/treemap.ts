/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Squarified treemap layout (Bruls, Huizing and van Wijk,
 * "Squarified Treemaps", 2000) and the nested group › industry › tile
 * layout of the heatmap, in CSS pixels.
 *
 * Items are laid out largest first. Each row (strip) of items is grown
 * while adding the next item does not make the row's worst aspect ratio
 * worse, then fixed along the shorter side of the remaining rectangle.
 * Areas are exactly proportional to the weights, rectangles never overlap
 * and stay inside the box.
 */

/** A rectangle in CSS pixels. */
export interface Box {
  /** Left edge. */
  x: number;
  /** Top edge. */
  y: number;
  /** Width, never negative. */
  w: number;
  /** Height, never negative. */
  h: number;
}

/** A weighted item to lay out. */
export interface Weighted<T> {
  /** Payload carried through the layout. */
  item: T;
  /** Positive finite weight; others are dropped. */
  weight: number;
}

/** A laid-out item. */
export interface Placed<T> {
  /** Payload. */
  item: T;
  /** Rectangle. */
  box: Box;
}

/**
 * Worst aspect ratio of a row laid along a side (Bruls et al., eq. 1).
 * @param areas Row areas.
 * @param side Length of the side the row is laid along.
 * @return Largest of max(w/h, h/w) over the row.
 */
function worst(areas: readonly number[], side: number): number {
  let sum = 0;
  let max = 0;
  let min = Infinity;
  for (const area of areas) {
    sum += area;
    if (area > max) max = area;
    if (area < min) min = area;
  }
  if (sum <= 0 || side <= 0) return Infinity;
  const side2 = side * side;
  const sum2 = sum * sum;
  return Math.max((side2 * max) / sum2, sum2 / (side2 * min));
}

/**
 * Squarifies weighted items into a box. Items with a non-positive or
 * non-finite weight are omitted; the rest are sorted by weight, largest
 * first (ties keep their input order).
 * @param rows Items and weights; the array is not mutated.
 * @param box Available rectangle.
 * @return One rectangle per kept item, in layout (descending) order.
 */
export function squarify<T>(
  rows: ReadonlyArray<Weighted<T>>,
  box: Box
): Array<Placed<T>> {
  const kept = rows
    .map((row, index) => ({...row, index}))
    .filter(row => Number.isFinite(row.weight) && row.weight > 0)
    .sort((a, b) => b.weight - a.weight || a.index - b.index);
  const total = kept.reduce((sum, row) => sum + row.weight, 0);
  const out: Array<Placed<T>> = [];
  if (!kept.length || box.w <= 0 || box.h <= 0) {
    return kept.map(row => ({item: row.item, box: {...box, w: 0, h: 0}}));
  }
  const scale = (box.w * box.h) / total;
  const areas = kept.map(row => row.weight * scale);
  let rect = {...box};
  let start = 0;
  while (start < kept.length) {
    const side = Math.min(rect.w, rect.h);
    let end = start + 1;
    while (
      end < kept.length &&
      worst(areas.slice(start, end + 1), side) <=
        worst(areas.slice(start, end), side)
    ) {
      end += 1;
    }
    // The last row takes the whole remaining rectangle, so rounding never
    // leaves a sliver outside the box.
    const rowAreas = areas.slice(start, end);
    const rowItems = kept.slice(start, end).map(row => row.item);
    const rowSum = rowAreas.reduce((a, b) => a + b, 0);
    const last = end === kept.length;
    if (rect.w >= rect.h) {
      // A column on the left of the remaining rectangle.
      const width = last ? rect.w : Math.min(rect.w, rowSum / rect.h);
      let y = rect.y;
      rowAreas.forEach((area, offset) => {
        const tail = offset === rowAreas.length - 1;
        const height = tail ? rect.y + rect.h - y : area / width;
        out.push({
          item: rowItems[offset] as T,
          box: {x: rect.x, y, w: width, h: Math.max(0, height)},
        });
        y += height;
      });
      rect = {x: rect.x + width, y: rect.y, w: rect.w - width, h: rect.h};
    } else {
      // A row along the top.
      const height = last ? rect.h : Math.min(rect.h, rowSum / rect.w);
      let x = rect.x;
      rowAreas.forEach((area, offset) => {
        const tail = offset === rowAreas.length - 1;
        const width = tail ? rect.x + rect.w - x : area / height;
        out.push({
          item: rowItems[offset] as T,
          box: {x, y: rect.y, w: Math.max(0, width), h: height},
        });
        x += width;
      });
      rect = {x: rect.x, y: rect.y + height, w: rect.w, h: rect.h - height};
    }
    start = end;
  }
  return out;
}

/**
 * Shrinks a box on every side.
 * @param box Box.
 * @param by Inset in pixels.
 * @return Inset box (never negative).
 */
export function inset(box: Box, by: number): Box {
  return {
    x: box.x + by,
    y: box.y + by,
    w: Math.max(0, box.w - 2 * by),
    h: Math.max(0, box.h - 2 * by),
  };
}

/** A tile reference with its weight. */
export interface TileWeight {
  /** Index of the tile in the caller's tile array. */
  index: number;
  /** Positive size weight. */
  weight: number;
}

/** Tiles of one industry (or the single unnamed level of a group). */
export interface IndustryInput {
  /** Industry name, or null when the group has one level. */
  name: string | null;
  /** Its tiles. */
  tiles: readonly TileWeight[];
}

/** One group (sector, asset class) or the single flat group. */
export interface GroupInput {
  /** Group name, or null for a flat map. */
  name: string | null;
  /** Industries of the group. */
  industries: readonly IndustryInput[];
}

/** A laid-out group frame. */
export interface GroupLayout {
  /** Group name, or null for a flat map. */
  name: string | null;
  /** Whole frame including the header. */
  box: Box;
  /** Header label strip, or null when the frame is too small for one. */
  header: Box | null;
  /** Industry rectangles inside the frame (thin separators between). */
  industries: Array<{name: string | null; box: Box}>;
}

/** A laid-out tile. */
export interface TileLayout {
  /** Index of the tile in the caller's tile array. */
  index: number;
  /** Tile rectangle (already inset by the tile gap). */
  box: Box;
  /** Group name, or null. */
  group: string | null;
  /** Industry name, or null. */
  industry: string | null;
}

/** Complete layout of a heatmap. */
export interface HeatmapLayout {
  /** Group frames, largest first. */
  groups: GroupLayout[];
  /** Tiles, in layout order. */
  tiles: TileLayout[];
}

/** Layout spacing in CSS pixels. */
export interface LayoutOptions {
  /** Height of a group's header strip. */
  headerHeight: number;
  /** Gap between group frames. */
  groupGap: number;
  /** Gap between industries of a group. */
  industryGap: number;
  /** Gap between tiles. */
  tileGap: number;
}

/** Default spacing: a 18 px header, 2/1/1 px gaps. */
export const DEFAULT_LAYOUT: LayoutOptions = {
  headerHeight: 18,
  groupGap: 2,
  industryGap: 1,
  tileGap: 0.5,
};

/**
 * Sums a list of tile weights.
 * @param tiles Tiles.
 * @return Total weight.
 */
function total(tiles: readonly TileWeight[]): number {
  return tiles.reduce((sum, tile) => sum + tile.weight, 0);
}

/**
 * Lays out groups of industries of tiles. Named groups get a header strip
 * when their frame is at least three header heights tall and 48 px wide.
 * @param groups Groups (a single unnamed group for a flat map).
 * @param box Available rectangle in CSS pixels.
 * @param options Spacing.
 * @return Group frames and tile rectangles.
 */
export function layoutHeatmap(
  groups: readonly GroupInput[],
  box: Box,
  options: LayoutOptions = DEFAULT_LAYOUT
): HeatmapLayout {
  const out: HeatmapLayout = {groups: [], tiles: []};
  const flat = groups.length === 1 && groups[0]?.name === null;
  const frames = squarify(
    groups.map(group => ({
      item: group,
      weight: group.industries.reduce((sum, row) => sum + total(row.tiles), 0),
    })),
    box
  );
  for (const frame of frames) {
    const group = frame.item;
    const outer = flat ? frame.box : inset(frame.box, options.groupGap / 2);
    const header =
      group.name !== null &&
      outer.h >= options.headerHeight * 3 &&
      outer.w >= 48
        ? {...outer, h: options.headerHeight}
        : null;
    const body = header
      ? {...outer, y: outer.y + header.h, h: outer.h - header.h}
      : outer;
    const layout: GroupLayout = {
      name: group.name,
      box: outer,
      header,
      industries: [],
    };
    const industries = squarify(
      group.industries.map(row => ({item: row, weight: total(row.tiles)})),
      body
    );
    for (const industry of industries) {
      const area =
        industries.length > 1
          ? inset(industry.box, options.industryGap / 2)
          : industry.box;
      layout.industries.push({name: industry.item.name, box: area});
      for (const tile of squarify(
        industry.item.tiles.map(row => ({item: row, weight: row.weight})),
        area
      )) {
        out.tiles.push({
          index: tile.item.index,
          box: inset(tile.box, options.tileGap),
          group: group.name,
          industry: industry.item.name,
        });
      }
    }
    out.groups.push(layout);
  }
  return out;
}
