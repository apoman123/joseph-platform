/**
 * @fileoverview Tests for the triangle pattern tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {trianglePatternTool as tool, triangleBoundaries} from '.';

describe('triangle-pattern', () => {
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '150'},
    {time_ms: 600_000, price: '110'},
    {time_ms: 1_200_000, price: '140'},
    {time_ms: 1_800_000, price: '120'},
  ]);
  it('has a valid manifest needing four anchors', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(4);
  });
  it('runs converging boundaries to the apex', () => {
    expect(
      triangleBoundaries([
        {x: 0, y: 250},
        {x: 100, y: 450},
        {x: 200, y: 300},
        {x: 300, y: 400},
      ])
    ).toEqual([
      [
        {x: 0, y: 250},
        {x: 450, y: 362.5},
      ],
      [
        {x: 100, y: 450},
        {x: 450, y: 362.5},
      ],
    ]);
  });
  it('stops diverging boundaries at the last anchor', () => {
    const lines = triangleBoundaries([
      {x: 0, y: 250},
      {x: 100, y: 450},
      {x: 200, y: 200},
      {x: 300, y: 500},
    ]);
    expect(lines?.[0][1].x).toBe(300);
    expect(lines?.[1][1].x).toBe(300);
    expect(triangleBoundaries([{x: 0, y: 0}])).toBeNull();
  });
  it('labels the points and fills between the boundaries', () => {
    expect(texts(renderOps(tool, doc))).toEqual(['A', 'B', 'C', 'D']);
    const unfilled = {...doc, style: {...doc.style, fill_color: null}};
    const fills = (d: typeof doc) =>
      renderOps(tool, d).filter(o => o.op === 'fill').length;
    expect(fills(doc) - fills(unfilled)).toBe(1);
  });
  it('hits handles, the zig-zag, the boundaries and the filled area', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 199, y: 301}, 6)).toEqual({
      part: 'handle',
      handleIndex: 2,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 375}, 6)).toEqual({
      part: 'body',
    });
    // Upper boundary beyond the last anchor, towards the apex.
    expect(tool.hitTest(doc, TRANSFORM, {x: 400, y: 350}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 50, y: 300}, 6)).toEqual({
      part: 'body',
    });
    const unfilled = {...doc, style: {...doc.style, fill_color: null}};
    expect(tool.hitTest(unfilled, TRANSFORM, {x: 50, y: 300}, 6)).toBeNull();
    expect(tool.hitTest(doc, TRANSFORM, {x: 600, y: 100}, 6)).toBeNull();
  });
});
