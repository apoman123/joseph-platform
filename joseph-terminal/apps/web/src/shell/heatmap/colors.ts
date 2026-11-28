/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Heatmap colour schemes, legend segmentation and readable
 * tile text colours.
 *
 * When the data or the colour metric changes, the legend finds the spread
 * of the metric's values and divides it into seven segments: symmetric
 * around zero for a change (e.g. −3% … +3% for a typical day, one segment
 * per step), from zero upwards for a one-directional metric such as
 * relative volume or volatility. The spread is the 95th percentile of the
 * magnitudes, so one outlier does not wash every other tile out to grey.
 * Every tile takes its segment's colour; a missing value is drawn in the
 * separate "no data" grey.
 */

/** Colour scheme id. */
export type SchemeId = 'classic' | 'colorblind' | 'mono';

/** A selectable colour scheme. */
export interface ColorScheme {
  /** Scheme id. */
  id: SchemeId;
  /** Menu label. */
  label: string;
  /** Seven diverging colours, strongest fall to strongest rise. */
  diverging: readonly string[];
  /** Seven sequential colours, weakest to strongest. */
  sequential: readonly string[];
  /** Colour of a tile without a value. */
  missing: string;
}

/** Number of legend segments. */
export const SEGMENTS = 7;

/** Colour schemes in menu order. */
export const SCHEMES: readonly ColorScheme[] = [
  {
    id: 'classic',
    label: 'Classic',
    diverging: [
      '#d93a40',
      '#a33a40',
      '#6d3940',
      '#45484f',
      '#2f6b4b',
      '#2a9256',
      '#27b862',
    ],
    sequential: [
      '#3c4147',
      '#334e5c',
      '#2e5d72',
      '#2a6d88',
      '#287e9f',
      '#2a8fb7',
      '#2fa1cf',
    ],
    missing: '#26292d',
  },
  {
    id: 'colorblind',
    label: 'Color-blind',
    diverging: [
      '#e07422',
      '#ad6230',
      '#77503a',
      '#46484e',
      '#305a80',
      '#2f72ab',
      '#3a8de0',
    ],
    sequential: [
      '#3c4147',
      '#4a4c3b',
      '#5e5833',
      '#74652b',
      '#8b7223',
      '#a3801c',
      '#bd8f16',
    ],
    missing: '#26292d',
  },
  {
    id: 'mono',
    label: 'Monochrome',
    diverging: [
      '#1d2024',
      '#2c3036',
      '#3d4249',
      '#52575f',
      '#6c727a',
      '#8a9098',
      '#abb1b8',
    ],
    sequential: [
      '#2c3036',
      '#3d4249',
      '#52575f',
      '#6c727a',
      '#8a9098',
      '#abb1b8',
      '#cdd2d7',
    ],
    missing: '#141618',
  },
];

/**
 * Finds a scheme.
 * @param id Scheme id; unknown ids get the classic scheme.
 * @return Scheme.
 */
export function scheme(id: string | undefined): ColorScheme {
  return SCHEMES.find(row => row.id === id) ?? (SCHEMES[0] as ColorScheme);
}

/** One legend segment. */
export interface LegendSegment {
  /** Segment index, 0 (lowest) to 6 (highest). */
  index: number;
  /** Segment colour. */
  color: string;
  /** Short label, e.g. `−3%`, `0%`, `≥ 2.5`. */
  label: string;
  /** Inclusive lower bound, or −Infinity. */
  from: number;
  /** Exclusive upper bound, or Infinity. */
  to: number;
}

/** A computed legend. */
export interface Legend {
  /** Segments in ascending order. */
  segments: LegendSegment[];
  /** Step between segment centres (diverging) or edges (sequential). */
  step: number;
  /** Colour scale. */
  scale: 'diverging' | 'sequential';
}

const NICE = [1, 2, 2.5, 5];

/**
 * Rounds a positive step to the nearest (in ratio) of 1, 2, 2.5 or 5
 * times a power of ten.
 * @param raw Raw step.
 * @return Nice step, at least 0.01.
 */
export function niceStep(raw: number): number {
  if (!Number.isFinite(raw) || raw <= 0.01) return 0.01;
  const power = 10 ** Math.floor(Math.log10(raw));
  let best = power;
  for (const factor of [...NICE, 10]) {
    const step = factor * power;
    if (Math.abs(Math.log(step / raw)) < Math.abs(Math.log(best / raw))) {
      best = step;
    }
  }
  return best;
}

/**
 * Returns a quantile of sorted values.
 * @param sorted Ascending finite values.
 * @param q Quantile in [0, 1].
 * @return Value at the quantile (0 for an empty list).
 */
function quantile(sorted: readonly number[], q: number): number {
  if (!sorted.length) return 0;
  const index = Math.min(sorted.length - 1, Math.floor(q * sorted.length));
  return sorted[index] ?? 0;
}

/**
 * Formats a legend bound.
 * @param value Number.
 * @param percent Whether the metric is a percentage.
 * @param signed Whether to show a + sign on positive values.
 * @return Label text.
 */
function boundLabel(value: number, percent: boolean, signed: boolean): string {
  const digits = Math.abs(value) >= 10 || Number.isInteger(value) ? 0 : 2;
  const text = Number(value.toFixed(digits)).toString();
  const sign = signed && value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${text.replace('-', '')}${percent ? '%' : ''}`;
}

/**
 * Builds the seven-segment legend of a metric's values.
 * @param values Values of the visible tiles (non-finite ones are ignored).
 * @param scale Diverging (signed change) or sequential (non-negative).
 * @param colors Scheme.
 * @param percent Whether the values are percentages (for labels).
 * @return Legend.
 */
export function buildLegend(
  values: readonly number[],
  scale: 'diverging' | 'sequential',
  colors: ColorScheme,
  percent: boolean
): Legend {
  const finite = values.filter(Number.isFinite);
  const palette = scale === 'diverging' ? colors.diverging : colors.sequential;
  if (scale === 'diverging') {
    const spread = quantile(
      finite.map(Math.abs).sort((a, b) => a - b),
      0.95
    );
    const step = niceStep(spread / 3);
    const segments = palette.map((color, index) => {
      const centre = (index - 3) * step;
      return {
        index,
        color,
        label: boundLabel(centre, percent, true),
        from: index === 0 ? -Infinity : centre - step / 2,
        to: index === SEGMENTS - 1 ? Infinity : centre + step / 2,
      };
    });
    return {segments, step, scale};
  }
  const spread = quantile(
    finite.map(value => Math.max(0, value)).sort((a, b) => a - b),
    0.95
  );
  const step = niceStep(spread / (SEGMENTS - 1));
  const segments = palette.map((color, index) => ({
    index,
    color,
    label:
      index === SEGMENTS - 1
        ? `≥ ${boundLabel(index * step, percent, false)}`
        : boundLabel(index * step, percent, false),
    from: index === 0 ? -Infinity : index * step,
    to: index === SEGMENTS - 1 ? Infinity : (index + 1) * step,
  }));
  return {segments, step, scale};
}

/**
 * Finds the segment of a value.
 * @param legend Legend.
 * @param value Value, or null when unavailable.
 * @return Segment index, or null for a missing value.
 */
export function segmentOf(legend: Legend, value: number | null): number | null {
  if (value === null || !Number.isFinite(value)) return null;
  const found = legend.segments.find(
    segment => value >= segment.from && value < segment.to
  );
  return found ? found.index : SEGMENTS - 1;
}

/**
 * Parses a `#rrggbb` colour.
 * @param hex Colour.
 * @return Red, green and blue in [0, 255].
 */
function rgb(hex: string): [number, number, number] {
  const value = Number(`0x${hex.slice(1, 7)}`);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

/**
 * WCAG relative luminance of a colour.
 * @param hex `#rrggbb` colour.
 * @return Luminance in [0, 1].
 */
export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map(channel => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * WCAG contrast ratio of two colours.
 * @param a First `#rrggbb` colour.
 * @param b Second `#rrggbb` colour.
 * @return Ratio in [1, 21].
 */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [
    number,
    number,
  ];
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Chooses white or near-black text, whichever contrasts more with a fill.
 * @param fill `#rrggbb` tile colour.
 * @return Text colour.
 */
export function textColor(fill: string): string {
  return contrast(fill, '#ffffff') >= contrast(fill, '#0b0c0e')
    ? '#ffffff'
    : '#0b0c0e';
}
