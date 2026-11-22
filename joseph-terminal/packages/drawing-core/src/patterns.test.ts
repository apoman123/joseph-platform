/**
 * @fileoverview Tests for the shared chart-pattern behaviour.
 */
import {describe, expect, it} from 'vitest';
import {createDrawing} from './document';
import {
  createPatternTool,
  hitTestPattern,
  labelOffset,
  legRatio,
  patternFillPolygons,
  ratioSummary,
  type PatternSpec,
} from './patterns';
import type {PixelTransform} from './surface';

/** 10 px per minute; price 200 at y=0 and 100 at y=500. */
const TRANSFORM: PixelTransform = {
  width: 1000,
  height: 500,
  timeToX: t => (t / 60_000) * 10,
  xToTime: x => (x / 10) * 60_000,
  priceToY: p => ((200 - p) / 100) * 500,
  yToPrice: y => 200 - (y / 500) * 100,
};

/** Three-point spec with one ratio, one fill and a guide. */
const SPEC: PatternSpec = {
  labels: ['A', 'B', 'C'],
  ratios: [{leg: [1, 2], of: [0, 1], along: [0, 2]}],
  fills: [[0, 1, 2]],
  guides: points => {
    const last = points[2];
    return last
      ? [
          {
            points: [last, {x: last.x + 100, y: last.y}],
            dashed: true,
          },
        ]
      : [];
  },
};

const ANCHORS = [
  {time_ms: 0, price: '100'},
  {time_ms: 600_000, price: '150'},
  {time_ms: 1_200_000, price: '130.9'},
];

/**
 * Builds a pattern document.
 * @param fill Fill color or null.
 * @param anchors Anchors.
 * @return Document.
 */
function doc(fill: string | null, anchors = ANCHORS) {
  return createDrawing({
    toolId: 'test-pattern',
    toolVersion: 1,
    instrumentId: 'mock:perp:BTCUSD',
    timeframe: null,
    anchors,
    style: {
      color: '#e66fd2',
      line_width: 1,
      line_style: 'solid',
      fill_color: fill,
    },
    zIndex: 1,
    nowMs: 0,
    newId: () => 'p1',
  });
}

describe('legRatio', () => {
  it('divides leg lengths exactly', () => {
    expect(legRatio(ANCHORS, [1, 2], [0, 1])).toBe('0.382');
    expect(legRatio(ANCHORS, [0, 1], [1, 2])).toBe('2.618');
  });
  it('is null for a flat reference leg or a missing anchor', () => {
    const flat = [
      {time_ms: 0, price: '100'},
      {time_ms: 1, price: '100'},
      {time_ms: 2, price: '120'},
    ];
    expect(legRatio(flat, [1, 2], [0, 1])).toBeNull();
    expect(legRatio(ANCHORS, [2, 3], [0, 1])).toBeNull();
  });
});

describe('ratioSummary', () => {
  it('names legs by their point labels', () => {
    expect(ratioSummary(SPEC, ANCHORS)).toBe('BC/AB 0.382');
    expect(ratioSummary({labels: ['A', 'B']}, ANCHORS)).toBe('');
  });
});

describe('labelOffset', () => {
  it('places labels above highs and below lows', () => {
    const points = [
      {x: 0, y: 300},
      {x: 10, y: 100},
      {x: 20, y: 400},
    ];
    expect(labelOffset(points, 1)).toBe(-12);
    expect(labelOffset(points, 2)).toBe(12);
    expect(labelOffset(points, 0)).toBe(12);
    expect(labelOffset([{x: 0, y: 0}], 0)).toBe(-12);
  });
});

describe('patternFillPolygons', () => {
  it('adds derived shapes only for complete patterns', () => {
    const spec: PatternSpec = {
      labels: ['A', 'B', 'C'],
      fillShapes: points => [[...points]],
    };
    const points = [
      {x: 0, y: 0},
      {x: 10, y: 0},
      {x: 10, y: 10},
    ];
    expect(patternFillPolygons(spec, points, TRANSFORM, true)).toHaveLength(1);
    expect(patternFillPolygons(spec, points, TRANSFORM, false)).toEqual([]);
  });
});

describe('hitTestPattern', () => {
  // Pixels: A (0, 500), B (100, 250), C (200, 345.5).
  it('returns the anchor index for handles', () => {
    expect(
      hitTestPattern(doc(null), TRANSFORM, {x: 99, y: 251}, 6, SPEC)
    ).toEqual({part: 'handle', handleIndex: 1});
  });
  it('hits legs, ratio connectors, guides and filled insides', () => {
    const plain = doc(null);
    expect(hitTestPattern(plain, TRANSFORM, {x: 50, y: 375}, 6, SPEC)).toEqual({
      part: 'body',
    });
    expect(
      hitTestPattern(plain, TRANSFORM, {x: 100, y: 422.75}, 6, SPEC)
    ).toEqual({part: 'body'});
    expect(
      hitTestPattern(plain, TRANSFORM, {x: 260, y: 345.5}, 6, SPEC)
    ).toEqual({part: 'body'});
    expect(
      hitTestPattern(plain, TRANSFORM, {x: 100, y: 330}, 6, SPEC)
    ).toBeNull();
    expect(
      hitTestPattern(doc('#e66fd21a'), TRANSFORM, {x: 100, y: 330}, 6, SPEC)
    ).toEqual({part: 'body'});
  });
  it('ignores guides of an incomplete pattern', () => {
    const partial = doc(null, ANCHORS.slice(0, 2));
    expect(
      hitTestPattern(partial, TRANSFORM, {x: 260, y: 345.5}, 6, SPEC)
    ).toBeNull();
  });
});

describe('createPatternTool', () => {
  const tool = createPatternTool(
    {
      id: 'test-pattern',
      version: 1,
      displayName: 'Test pattern',
      group: 'patterns',
      icon: 'route',
      anchorCount: 3,
    },
    SPEC,
    'off'
  );
  const palette = {
    user: '#e66fd2',
    text: '#8fa5b3',
    bid: '#3cc79a',
    ask: '#f0645a',
    background: '#0e1c25',
    fontFamily: 'Archivo',
  };
  it('keeps the fill key so the style bar can offer it', () => {
    expect(tool.defaultStyle(palette)).toMatchObject({
      color: '#e66fd2',
      fill_color: null,
      show_labels: true,
    });
    expect(tool.describe?.(doc(null))).toBe('BC/AB 0.382');
  });
  it('falls back to the spec description without ratios', () => {
    const plain = createPatternTool(tool.manifest, {
      labels: ['A', 'B', 'C'],
      describe: anchors => `From ${anchors[0]?.price}`,
    });
    expect(plain.defaultStyle(palette).fill_color).toBeUndefined();
    expect(plain.describe?.(doc(null))).toBe('From 100');
  });
});
