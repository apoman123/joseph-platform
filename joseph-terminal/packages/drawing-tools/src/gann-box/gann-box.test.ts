/**
 * @fileoverview Tests for the Gann box tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {GANN_BOX_LEVELS, gannBoxGrid, gannBoxTool as tool} from '.';

describe('gann-box', () => {
  // A (100, 400), B (500, 0): a 400 x 400 px box.
  const doc = makeDoc(tool, [
    {time_ms: 600_000, price: '120'},
    {time_ms: 3_000_000, price: '200'},
  ]);

  it('has a valid manifest', () => expect(manifestProblems(tool)).toEqual([]));

  it('divides time and price from the origin corner', () => {
    const grid = gannBoxGrid({x: 100, y: 400}, {x: 500, y: 0}, [
      '0',
      '0.25',
      '0.5',
      '1',
    ]);
    expect(grid.vertical.map(v => v.x)).toEqual([100, 200, 300, 500]);
    expect(grid.horizontal.map(h => h.y)).toEqual([400, 300, 200, 0]);
  });

  it('labels both edges with every ratio', () => {
    const labels = texts(renderOps(tool, doc));
    expect(labels).toHaveLength(GANN_BOX_LEVELS.length * 2);
    expect(labels).toContain('0.382');
  });

  it('hits handles, grid lines, diagonals and the filled inside', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 498, y: 3}, 6)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    // 0.25 time division at x = 200.
    expect(tool.hitTest(doc, TRANSFORM, {x: 202, y: 350}, 6)).toEqual({
      part: 'body',
    });
    // Diagonal A-B passes (300, 200).
    expect(tool.hitTest(doc, TRANSFORM, {x: 301, y: 201}, 3)).toEqual({
      part: 'body',
    });
    // Inside a cell, away from lines: only hit when filled.
    expect(tool.hitTest(doc, TRANSFORM, {x: 230, y: 345}, 3)).toEqual({
      part: 'body',
    });
    const unfilled = {...doc, style: {...doc.style, fill_color: null}};
    expect(tool.hitTest(unfilled, TRANSFORM, {x: 230, y: 345}, 3)).toBeNull();
    expect(tool.hitTest(doc, TRANSFORM, {x: 700, y: 300}, 6)).toBeNull();
  });
});
