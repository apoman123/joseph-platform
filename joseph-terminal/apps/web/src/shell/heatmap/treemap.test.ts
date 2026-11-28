/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Squarified treemap invariants: every rectangle inside the
 * box, no overlaps, areas proportional to the weights, sensible aspect
 * ratios, and the nested group/industry layout.
 */

import {describe, expect, it} from 'vitest';
import {layoutHeatmap, squarify, type Box} from './treemap';

const EPS = 1e-6;

/**
 * Checks that two boxes do not overlap (touching edges are allowed).
 * @param a First box.
 * @param b Second box.
 * @return Whether the interiors are disjoint.
 */
function disjoint(a: Box, b: Box): boolean {
  return (
    Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x) <= EPS ||
    Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y) <= EPS
  );
}

/**
 * Asserts that boxes lie inside a container.
 * @param boxes Boxes.
 * @param box Container.
 */
function expectInside(boxes: readonly Box[], box: Box): void {
  for (const cell of boxes) {
    expect(cell.x).toBeGreaterThanOrEqual(box.x - EPS);
    expect(cell.y).toBeGreaterThanOrEqual(box.y - EPS);
    expect(cell.x + cell.w).toBeLessThanOrEqual(box.x + box.w + EPS);
    expect(cell.y + cell.h).toBeLessThanOrEqual(box.y + box.h + EPS);
  }
}

describe('squarify', () => {
  const box = {x: 10, y: 20, w: 900, h: 500};
  const weights = Array.from({length: 120}, (_, i) => 1 + ((i * 37) % 97));
  const cells = squarify(
    weights.map((weight, item) => ({item, weight})),
    box
  );

  it('places every positive item inside the box without overlap', () => {
    expect(cells).toHaveLength(weights.length);
    expectInside(
      cells.map(cell => cell.box),
      box
    );
    for (let i = 0; i < cells.length; i += 1) {
      for (let j = i + 1; j < cells.length; j += 1) {
        expect(
          disjoint(
            (cells[i] as (typeof cells)[number]).box,
            (cells[j] as (typeof cells)[number]).box
          )
        ).toBe(true);
      }
    }
  });

  it('gives areas proportional to the weights and fills the box', () => {
    const total = weights.reduce((a, b) => a + b, 0);
    let area = 0;
    for (const cell of cells) {
      const expected = ((weights[cell.item] ?? 0) / total) * box.w * box.h;
      expect(cell.box.w * cell.box.h).toBeCloseTo(expected, 3);
      area += cell.box.w * cell.box.h;
    }
    expect(area).toBeCloseTo(box.w * box.h, 3);
  });

  it('lays out largest first with near-square tiles', () => {
    const order = cells.map(cell => weights[cell.item] ?? 0);
    expect(order).toEqual([...order].sort((a, b) => b - a));
    const ratios = cells.map(
      cell =>
        Math.max(cell.box.w, cell.box.h) / Math.min(cell.box.w, cell.box.h)
    );
    const median = [...ratios].sort((a, b) => a - b)[ratios.length >> 1];
    expect(median).toBeLessThan(2.5);
  });

  it('drops non-positive weights and handles empty input', () => {
    const out = squarify(
      [
        {item: 'a', weight: 3},
        {item: 'b', weight: 0},
        {item: 'c', weight: Number.NaN},
        {item: 'd', weight: 1},
      ],
      {x: 0, y: 0, w: 100, h: 100}
    );
    expect(out.map(cell => cell.item)).toEqual(['a', 'd']);
    expect(squarify([], box)).toEqual([]);
  });
});

describe('layoutHeatmap', () => {
  it('nests industries in sector frames with header strips', () => {
    const box = {x: 0, y: 0, w: 800, h: 600};
    const layout = layoutHeatmap(
      [
        {
          name: 'Technology',
          industries: [
            {
              name: 'Software',
              tiles: [
                {index: 0, weight: 50},
                {index: 1, weight: 20},
              ],
            },
            {name: 'Semiconductors', tiles: [{index: 2, weight: 30}]},
          ],
        },
        {
          name: 'Energy',
          industries: [{name: 'Oil', tiles: [{index: 3, weight: 25}]}],
        },
      ],
      box
    );
    expect(layout.groups.map(group => group.name)).toEqual([
      'Technology',
      'Energy',
    ]);
    expect(layout.groups.every(group => group.header !== null)).toBe(true);
    expect(layout.tiles).toHaveLength(4);
    expectInside(
      layout.tiles.map(tile => tile.box),
      box
    );
    const tech = layout.groups[0];
    for (const tile of layout.tiles.filter(row => row.group === 'Technology')) {
      expectInside([tile.box], tech?.box ?? box);
      // Tiles sit below the header strip.
      expect(tile.box.y).toBeGreaterThanOrEqual(
        (tech?.header?.y ?? 0) + (tech?.header?.h ?? 0) - EPS
      );
    }
    for (const a of layout.tiles) {
      for (const b of layout.tiles) {
        if (a !== b) expect(disjoint(a.box, b.box)).toBe(true);
      }
    }
    expect(tech?.industries.map(row => row.name).sort()).toEqual([
      'Semiconductors',
      'Software',
    ]);
  });

  it('draws a flat map edge to edge without headers', () => {
    const layout = layoutHeatmap(
      [
        {
          name: null,
          industries: [
            {
              name: null,
              tiles: [
                {index: 0, weight: 2},
                {index: 1, weight: 1},
              ],
            },
          ],
        },
      ],
      {x: 0, y: 0, w: 300, h: 200}
    );
    expect(layout.groups[0]?.header).toBeNull();
    expect(layout.tiles).toHaveLength(2);
  });
});
