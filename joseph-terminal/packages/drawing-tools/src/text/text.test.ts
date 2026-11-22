/**
 * @fileoverview Tests for the text note tool.
 */
import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {noteLines, textTool as tool} from '.';

describe('text', () => {
  const doc = makeDoc(tool, [{time_ms: 0, price: '150'}]);
  it('has a valid manifest', () => expect(manifestProblems(tool)).toEqual([]));
  it('renders and wraps note text', () => {
    expect(texts(renderOps(tool, doc))).toEqual(['Liquidity above']);
    expect(noteLines('a'.repeat(60))).toHaveLength(2);
  });
  it('hits the text box', () => {
    expect(tool.hitTest(doc, TRANSFORM, {x: 30, y: 255}, 2)).toEqual({
      part: 'body',
    });
  });
});
