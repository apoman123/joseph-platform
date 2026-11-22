/**
 * @fileoverview Tests for the first-party tool list.
 */
import {ToolRegistry} from '@joseph/drawing-core';
import {describe, expect, it} from 'vitest';
import {FIRST_PARTY_TOOLS} from '.';

describe('FIRST_PARTY_TOOLS', () => {
  it('registers every tool with a unique id', () => {
    const registry = new ToolRegistry();
    for (const tool of FIRST_PARTY_TOOLS) registry.register(tool);
    expect(registry.list()).toHaveLength(18);
    expect(registry.list().map(t => t.manifest.id)).toEqual(
      expect.arrayContaining([
        'ray',
        'extended-line',
        'arrow',
        'polyline',
        'brush',
        'zone-box',
      ])
    );
  });

  it('removing a tool leaves the others working', () => {
    const registry = new ToolRegistry();
    const disposers = FIRST_PARTY_TOOLS.map(tool => registry.register(tool));
    disposers[
      FIRST_PARTY_TOOLS.findIndex(t => t.manifest.id === 'fib-retracement')
    ]?.();
    expect(registry.get('fib-retracement')).toBeUndefined();
    expect(registry.get('trendline')).toBeDefined();
  });
});
