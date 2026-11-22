/**
 * @fileoverview Tests for the brush tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {brushTool as tool} from '.';

describe('brush', () => {
  const doc = makeDoc(tool, [
    {time_ms: 0, price: '150'},
    {time_ms: 120_000, price: '152'},
    {time_ms: 240_000, price: '149'},
    {time_ms: 360_000, price: '151'},
  ]);
  it('has a valid manifest and draws freehand', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.creation).toBe('freehand');
  });
  it('draws a smooth curve with handles at both ends', () => {
    const ops = renderOps(tool, doc);
    expect(ops.filter(o => o.op === 'quadraticCurveTo')).toHaveLength(2);
    expect(ops.filter(o => o.op === 'rect')).toHaveLength(2);
  });
  it('maps the end handle to the last anchor', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 60, y: 245}, 6)).toEqual({
      part: 'handle',
      handleIndex: 3,
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 10, y: 245}, 6)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(doc, TRANSFORM, {x: 500, y: 10}, 6)).toBeNull();
  });
});
