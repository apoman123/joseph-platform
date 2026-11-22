/**
 * @fileoverview Tests for the Gann fan tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {gannFanRays, gannFanTool as tool} from '.';

describe('gann-fan', () => {
  // A (0, 500), B (100, 400): the 1×1 ray climbs 1px per px.
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '100'},
    {time_ms: 600_000, price: '120'},
  ]);

  it('has a valid manifest', () => expect(manifestProblems(tool)).toEqual([]));

  it('builds nine rays with 1×1 through B, clipped to the plot', () => {
    const rays = gannFanRays({x: 0, y: 500}, {x: 100, y: 400}, 1000, 500);
    expect(rays.map(r => r.label)).toEqual([
      '1×8',
      '1×4',
      '1×3',
      '1×2',
      '1×1',
      '2×1',
      '3×1',
      '4×1',
      '8×1',
    ]);
    const oneByOne = rays[4];
    expect(oneByOne?.direction).toEqual({x: 100, y: -100});
    expect(oneByOne?.end.x).toBeCloseTo(500);
    expect(oneByOne?.end.y).toBeCloseTo(0);
    // 1×2: half the slope, leaves through the right edge.
    expect(rays[3]?.direction).toEqual({x: 200, y: -100});
    expect(rays[3]?.end.x).toBeCloseTo(1000);
    expect(rays[3]?.end.y).toBeCloseTo(0);
    // 2×1: twice the slope.
    expect(rays[5]?.direction).toEqual({x: 100, y: -200});
    expect(rays[5]?.end.x).toBeCloseTo(250);
    expect(gannFanRays({x: 5, y: 5}, {x: 5, y: 5}, 1000, 500)).toEqual([]);
  });

  it('labels every ray', () => {
    expect(texts(renderOps(tool, doc))).toHaveLength(9);
    expect(texts(renderOps(tool, doc))).toContain('1×1');
  });

  it('hits handles by index and rays, misses between rays', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 2, y: 498}, 6)).toEqual({
      part: 'handle',
      handleIndex: 0,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 300, y: 201}, 6)).toEqual({
      part: 'body',
    });
    // Between 1×1 (y = 200 at x = 300) and 1×2 (y = 350).
    expect(tool.hitTest(doc, TRANSFORM, {x: 300, y: 280}, 6)).toBeNull();
  });
});
