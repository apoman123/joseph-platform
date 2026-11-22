/**
 * @fileoverview Tests for the extended line tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
} from '../testing/harness';
import {extendedLineTool} from '.';

const ANCHORS = [
  {time_ms: 600_000, price: '150'},
  {time_ms: 1_200_000, price: '160'},
];

describe('extended line', () => {
  const doc = makeDoc(extendedLineTool, ANCHORS);
  it('has a valid manifest', () =>
    expect(manifestProblems(extendedLineTool)).toEqual([]));
  it('spans both directions', () => {
    const ops = renderOps(extendedLineTool, doc);
    expect(ops.find(o => o.op === 'moveTo')?.args[0]).toBe(0);
    expect(
      extendedLineTool.hitTest(doc, TRANSFORM, {x: 50, y: 275}, 6)
    ).toEqual({part: 'body'});
    expect(
      extendedLineTool.hitTest(doc, TRANSFORM, {x: 100, y: 250}, 6)
    ).toEqual({
      part: 'handle',
      handleIndex: 0,
    });
  });
});
