/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the Fibonacci extension tool.
 */

import {describe, expect, it} from 'vitest';
import {
  TRANSFORM,
  makeDoc,
  manifestProblems,
  renderOps,
  texts,
} from '../testing/harness';
import {extensionPrices, fibExtensionTool as tool} from '.';

describe('fib-extension', () => {
  it('has a valid manifest needing three anchors', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest.anchorCount).toBe(3);
  });
  it('projects the A-B leg from C', () => {
    expect(extensionPrices('100', '150', '120', ['1', '1.618'], '0.1')).toEqual(
      [
        {level: '1', price: '170'},
        {level: '1.618', price: '200.9'},
      ]
    );
  });
  it('renders level labels', () => {
    const doc = makeDoc(tool, [
      {time_ms: 0, price: '100'},
      {time_ms: 600_000, price: '150'},
      {time_ms: 900_000, price: '120'},
    ]);
    expect(texts(renderOps(tool, doc))).toContain('1 (170.0)');
    expect(tool.hitTest(doc, TRANSFORM, {x: 0, y: 500}, 6)).toEqual({
      part: 'handle',
      handleIndex: 0,
    });
  });
  it('draws the levels stored in props', () => {
    const doc = makeDoc(tool, [
      {time_ms: 0, price: '100'},
      {time_ms: 600_000, price: '150'},
      {time_ms: 900_000, price: '120'},
    ]);
    const custom = {...doc, props: {levels: ['0.5', '3']}};
    expect(texts(renderOps(tool, custom))).toEqual([
      '0.5 (145.0)',
      '3 (270.0)',
    ]);
    expect(texts(renderOps(tool, {...doc, props: {levels: []}}))).toEqual([]);
  });
});
