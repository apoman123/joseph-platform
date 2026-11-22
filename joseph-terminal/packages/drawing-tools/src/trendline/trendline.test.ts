/**
 * @fileoverview Tests for the trendline tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {trendlineTool as tool} from '.';

describe('trendline', () => {
  const doc = makeDoc(tool, [
    {time_ms: 600_000, price: '150'},
    {time_ms: 1_200_000, price: '160'},
  ]);
  it('has a valid manifest', () => expect(manifestProblems(tool)).toEqual([]));
  it('draws between anchors and extends when asked', () => {
    expect(renderOps(tool, doc).find(o => o.op === 'moveTo')?.args).toEqual([
      100, 250,
    ]);
    const extended = {
      ...doc,
      style: {...doc.style, extend_left: true, extend_right: true},
    };
    const ops = renderOps(tool, extended);
    expect(ops.find(o => o.op === 'moveTo')?.args[0]).toBe(0);
    expect(tool.hitTest(extended, TRANSFORM, {x: 500, y: 50}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 500, y: 50}, 6)).toBeNull();
  });
  it('hits handles before the body', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 200, y: 200}, 6)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 150, y: 225}, 6)).toEqual({
      part: 'body',
    });
  });
});
