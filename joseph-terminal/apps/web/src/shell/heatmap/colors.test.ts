/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Legend segmentation, colour schemes and readable tile
 * text.
 */

import {describe, expect, it} from 'vitest';
import {
  SCHEMES,
  SEGMENTS,
  buildLegend,
  contrast,
  niceStep,
  scheme,
  segmentOf,
  textColor,
} from './colors';

describe('legend segmentation', () => {
  it('divides a typical day into seven 1% segments around zero', () => {
    const values = [-3.2, -2.1, -1, -0.4, 0, 0.3, 1.2, 2.2, 2.9, 3.1];
    const legend = buildLegend(values, 'diverging', scheme('classic'), true);
    expect(legend.segments).toHaveLength(SEGMENTS);
    expect(legend.step).toBe(1);
    expect(legend.segments.map(row => row.label)).toEqual([
      '−3%',
      '−2%',
      '−1%',
      '0%',
      '+1%',
      '+2%',
      '+3%',
    ]);
    expect(segmentOf(legend, 0.2)).toBe(3);
    expect(segmentOf(legend, -0.6)).toBe(2);
    expect(segmentOf(legend, 2.6)).toBe(6);
    expect(segmentOf(legend, -40)).toBe(0);
    expect(segmentOf(legend, 40)).toBe(6);
    expect(segmentOf(legend, null)).toBeNull();
  });

  it('ignores a single outlier when finding the spread', () => {
    const values = [...Array.from({length: 99}, (_, i) => (i % 5) - 2), 80];
    const legend = buildLegend(values, 'diverging', scheme('classic'), true);
    expect(legend.step).toBeLessThanOrEqual(1);
  });

  it('starts one-directional metrics at zero', () => {
    const legend = buildLegend(
      [0.4, 0.9, 1.1, 2.5, 3],
      'sequential',
      scheme('classic'),
      false
    );
    expect(legend.step).toBe(0.5);
    expect(legend.segments[0]?.label).toBe('0');
    expect(legend.segments[6]?.label).toBe('≥ 3');
    expect(segmentOf(legend, 0.2)).toBe(0);
    expect(segmentOf(legend, 1.1)).toBe(2);
    expect(segmentOf(legend, 9)).toBe(6);
  });

  it('rounds steps to the nearest 1, 2, 2.5 or 5 times a power of ten', () => {
    expect(niceStep(0.7)).toBe(0.5);
    expect(niceStep(1.07)).toBe(1);
    expect(niceStep(2.2)).toBe(2);
    expect(niceStep(3)).toBe(2.5);
    expect(niceStep(4)).toBe(5);
    expect(niceStep(13)).toBe(10);
    expect(niceStep(0)).toBe(0.01);
  });
});

describe('colour schemes', () => {
  it('offers classic, colour-blind and monochrome palettes of seven', () => {
    expect(SCHEMES.map(row => row.id)).toEqual([
      'classic',
      'colorblind',
      'mono',
    ]);
    for (const row of SCHEMES) {
      expect(row.diverging).toHaveLength(SEGMENTS);
      expect(row.sequential).toHaveLength(SEGMENTS);
    }
    expect(scheme('unknown').id).toBe('classic');
  });

  it('keeps falls red, rises green and the middle grey in Classic', () => {
    const [down, , , middle, , , up] = scheme('classic').diverging.map<
      [number, number, number]
    >(hex => [
      Number(`0x${hex.slice(1, 3)}`),
      Number(`0x${hex.slice(3, 5)}`),
      Number(`0x${hex.slice(5, 7)}`),
    ]);
    expect(down?.[0]).toBeGreaterThan(down?.[1] ?? 0);
    expect(up?.[1]).toBeGreaterThan(up?.[0] ?? 0);
    expect(
      Math.max(...(middle ?? [])) - Math.min(...(middle ?? []))
    ).toBeLessThan(16);
  });

  it('avoids red/green in the colour-blind palette', () => {
    const [low, , , , , , high] = scheme('colorblind').diverging;
    expect(low).not.toBe(scheme('classic').diverging[0]);
    // Rises are blue: the blue channel dominates.
    expect(Number(`0x${high?.slice(5, 7)}`)).toBeGreaterThan(
      Number(`0x${high?.slice(1, 3)}`)
    );
  });

  it('chooses tile text with at least 4.5:1 contrast on every colour', () => {
    for (const row of SCHEMES) {
      for (const fill of [...row.diverging, ...row.sequential, row.missing]) {
        expect(contrast(fill, textColor(fill))).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});
